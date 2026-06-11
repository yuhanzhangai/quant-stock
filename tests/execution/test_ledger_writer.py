"""LedgerWriter 测试:schema/signals 幂等/状态机白名单含 correction/fills 幂等与作废对/
pdt/account/agent_runs/水位(ORDER_LEDGER_SPEC r3 §9 P1 清单)。"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from src.execution.ledger import LedgerWriteError, LedgerWriter, signal_id_for

CALL_TS = datetime(2026, 6, 10, 14, 30, tzinfo=UTC)
SUBMIT_TS = datetime(2026, 6, 10, 15, 31, tzinfo=UTC)
FILL_TS = datetime(2026, 6, 10, 15, 32, tzinfo=UTC)


@pytest.fixture
def writer(tmp_path):
    w = LedgerWriter(tmp_path / "ledger.duckdb")
    yield w
    w.close()


def make_signal(writer: LedgerWriter, tweet_id: str = "1932", ticker: str = "NVDA") -> str:
    sid = signal_id_for(tweet_id, ticker)
    writer.insert_signal(
        signal_id=sid,
        tweet_id=tweet_id,
        handle="@proven_caller",
        tier="PROVEN",
        tier_csv_date=date(2026, 6, 10),
        ticker=ticker,
        direction="bullish",
        call_ts=CALL_TS,
        tweet_text="NVDA to the moon",
        tweet_url="https://x.com/x/status/1932",
        decision="followed",
        decision_reason="all_gates_passed",
        rule_version="v1.0",
    )
    return sid


def make_order(writer: LedgerWriter, sid: str, **kw) -> str:
    defaults = dict(
        signal_id=sid,
        ticker="NVDA",
        side="buy",
        qty=Decimal("10"),
        order_type="limit",
        limit_price=Decimal("100.50"),
        submitted_ts=SUBMIT_TS,
        rule_version="v1.0",
    )
    defaults.update(kw)
    return writer.open_order(**defaults)


# ── schema / meta ────────────────────────────────────────────────────────


def test_schema_applies_idempotently(tmp_path):
    path = tmp_path / "ledger.duckdb"
    w1 = LedgerWriter(path)
    w1.close()
    w2 = LedgerWriter(path)  # 重开不报错,ledger_meta 不重复
    n = w2.conn.execute("SELECT count(*) FROM ledger_meta").fetchone()[0]
    assert n == 1
    w2.close()


def test_views_exist(writer):
    for view in (
        "v_orders_current",
        "v_fills_effective",
        "v_order_filled",
        "v_positions_eod",
        "v_recon_ledger_qty",
        "v_pdt_latest",
    ):
        writer.conn.execute(f"SELECT * FROM {view}")


# ── signals ──────────────────────────────────────────────────────────────


def test_signal_insert_and_idempotent_skip(writer):
    sid = make_signal(writer)
    assert sid == "sig_1932_NVDA"
    # 轮询重看同一喊单:跳过不重复插行
    again = writer.insert_signal(
        signal_id=sid,
        tweet_id="1932",
        handle="@proven_caller",
        tier="PROVEN",
        tier_csv_date=date(2026, 6, 10),
        ticker="NVDA",
        direction="bullish",
        call_ts=CALL_TS,
        tweet_text="NVDA to the moon",
        tweet_url="https://x.com/x/status/1932",
        decision="followed",
        decision_reason="all_gates_passed",
        rule_version="v1.0",
    )
    assert again is False
    assert writer.conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 1


def test_same_tweet_multi_ticker_no_collision(writer):
    make_signal(writer, tweet_id="1932", ticker="NVDA")
    make_signal(writer, tweet_id="1932", ticker="AMD")  # 同帖多 ticker 是真实场景(r2)
    assert writer.conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 2


# ── orders 状态机 ─────────────────────────────────────────────────────────


def test_order_lifecycle_legal_chain(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    assert writer.order_current(oid)["status"] == "submitted"
    assert writer.append_order_event(order_id=oid, status="partial") == 1
    assert writer.append_order_event(order_id=oid, status="filled") == 2
    cur = writer.order_current(oid)
    assert cur["status"] == "filled"
    assert cur["seq"] == 2
    # 不可变字段被复制,单行自含可读
    assert cur["qty"] == Decimal("10")
    assert cur["limit_price"] == Decimal("100.50")
    assert cur["submitted_ts"] is not None


def test_order_expired_terminal(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    writer.append_order_event(order_id=oid, status="partial")
    writer.append_order_event(order_id=oid, status="expired")  # r3:partial→expired 合法
    assert writer.order_current(oid)["status"] == "expired"


def test_illegal_transition_rejected(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    with pytest.raises(LedgerWriteError, match="非法状态迁移"):
        writer.append_order_event(order_id=oid, status="submitted")  # 不能原地迁移
    writer.append_order_event(order_id=oid, status="filled")
    with pytest.raises(LedgerWriteError, match="非法状态迁移"):
        writer.append_order_event(order_id=oid, status="cancelled")  # 终态封锁
    # 拒写=没落账
    assert writer.order_current(oid)["seq"] == 1


def test_correction_exempts_terminal_lock(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    writer.append_order_event(order_id=oid, status="filled")  # 误记终态
    # 更正行豁免终态封锁(r3 Exec④)
    seq = writer.append_order_event(order_id=oid, status="partial", corrects_seq=1, note="回采解析错误,实际仅部分成交")
    assert seq == 2
    cur = writer.order_current(oid)
    assert cur["status"] == "partial"  # v_orders_current 自动以更正为准
    assert cur["corrects_seq"] == 1
    # 原错误行永久留存可审
    assert (
        writer.conn.execute("SELECT status FROM orders WHERE order_id = ? AND seq = 1", [oid]).fetchone()[0] == "filled"
    )
    # 更正后订单回到非终态,状态机可继续推进
    writer.append_order_event(order_id=oid, status="filled")


def test_correction_requires_note_and_valid_seq(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    with pytest.raises(LedgerWriteError, match="note 必填"):
        writer.append_order_event(order_id=oid, status="cancelled", corrects_seq=0)
    with pytest.raises(LedgerWriteError, match="已存在的事件"):
        writer.append_order_event(order_id=oid, status="cancelled", corrects_seq=5, note="x")


def test_open_order_duplicate_rejected(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    with pytest.raises(LedgerWriteError, match="不可重复"):
        make_order(writer, sid, order_id=oid)


def test_unknown_order_event_rejected(writer):
    with pytest.raises(LedgerWriteError, match="订单不存在"):
        writer.append_order_event(order_id="ord_NOPE", status="filled")


def test_exit_reason_kill_switch_consistency(writer):
    sid = make_signal(writer)
    with pytest.raises(LedgerWriteError, match="kill_switch_engaged"):
        make_order(writer, sid, side="sell", exit_reason="kill_switch", kill_switch_engaged=False)
    oid = make_order(writer, sid, side="sell", exit_reason="kill_switch", kill_switch_engaged=True)
    assert writer.order_current(oid)["exit_reason"] == "kill_switch"


def test_broker_order_ref_backfill_on_later_event(writer):
    # 落账时序约定:seq=0 可能没读到券商订单号,回采后事件行补记并向后沿用
    sid = make_signal(writer)
    oid = make_order(writer, sid, note="confirmation_unverified")
    assert writer.order_current(oid)["broker_order_ref"] is None
    writer.append_order_event(order_id=oid, status="partial", broker_order_ref="FT-12345")
    writer.append_order_event(order_id=oid, status="filled")  # 未显式给 ref,沿用已记录值
    assert writer.order_current(oid)["broker_order_ref"] == "FT-12345"


# ── fills 幂等与作废对 ────────────────────────────────────────────────────


def test_fill_idempotent_dedup(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    fid = writer.insert_fill(
        order_id=oid,
        fill_ts=FILL_TS,
        qty=Decimal("10"),
        price=Decimal("100.40"),
        raw_text="Filled 10 NVDA @ 100.40",
    )
    assert fid is not None
    # 轮询重读同一订单页:同自然键跳过
    again = writer.insert_fill(
        order_id=oid,
        fill_ts=FILL_TS,
        qty=Decimal("10"),
        price=Decimal("100.40"),
        raw_text="Filled 10 NVDA @ 100.40 (re-scrape)",
    )
    assert again is None
    assert writer.conn.execute("SELECT count(*) FROM fills").fetchone()[0] == 1


def test_fill_requires_existing_order(writer):
    with pytest.raises(LedgerWriteError, match="订单不存在"):
        writer.insert_fill(
            order_id="ord_NOPE",
            fill_ts=FILL_TS,
            qty=Decimal("1"),
            price=Decimal("1"),
            raw_text="x",
        )


def test_void_pair_and_corrected_fill(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    bad = writer.insert_fill(
        order_id=oid,
        fill_ts=FILL_TS,
        qty=Decimal("10"),
        price=Decimal("100.40"),
        raw_text="解析错的行",
    )
    writer.void_fill(fill_id=bad, note="价格解析错误,实际 100.04")
    good = writer.insert_fill(
        order_id=oid,
        fill_ts=FILL_TS,
        qty=Decimal("10"),
        price=Decimal("100.04"),
        raw_text="Filled 10 NVDA @ 100.04",
    )
    # 作废对被剔除,只剩正确行;原始行留存可审
    eff = writer.conn.execute("SELECT fill_id FROM v_fills_effective").fetchall()
    assert eff == [(good,)]
    assert writer.conn.execute("SELECT count(*) FROM fills").fetchone()[0] == 3
    # 作废后同自然键可重插(幂等查重对 v_fills_effective,不含已作废行)
    agg = writer.conn.execute(
        "SELECT filled_qty, avg_fill_price FROM v_order_filled WHERE order_id = ?", [oid]
    ).fetchone()
    assert agg[0] == Decimal("10")
    assert agg[1] == pytest.approx(100.04)


def test_void_guards(writer):
    sid = make_signal(writer)
    oid = make_order(writer, sid)
    fid = writer.insert_fill(
        order_id=oid,
        fill_ts=FILL_TS,
        qty=Decimal("1"),
        price=Decimal("2"),
        raw_text="x",
    )
    with pytest.raises(LedgerWriteError, match="不存在"):
        writer.void_fill(fill_id="fil_NOPE", note="x")
    void_id = writer.void_fill(fill_id=fid, note="错行")
    with pytest.raises(LedgerWriteError, match="已被作废"):
        writer.void_fill(fill_id=fid, note="再废一次")
    with pytest.raises(LedgerWriteError, match="不可再被作废"):
        writer.void_fill(fill_id=void_id, note="废作废行")


# ── 对账视图 / pdt / account / agent_runs / 水位 ─────────────────────────


def test_recon_ledger_qty(writer):
    sid = make_signal(writer)
    buy = make_order(writer, sid)
    sell = make_order(writer, sid, side="sell", exit_reason="hold_21d")
    writer.insert_fill(order_id=buy, fill_ts=FILL_TS, qty=Decimal("10"), price=Decimal("100"), raw_text="b")
    writer.insert_fill(order_id=sell, fill_ts=FILL_TS, qty=Decimal("4"), price=Decimal("110"), raw_text="s")
    qty = writer.conn.execute("SELECT ledger_qty FROM v_recon_ledger_qty WHERE ticker = 'NVDA'").fetchone()[0]
    assert qty == Decimal("6")


def test_pdt_latest_snapshot(writer):
    writer.insert_pdt_entry(
        trade_date=date(2026, 6, 9),
        event_type="eod_snapshot",
        day_trades_5d=1,
        settled_cash=Decimal("20000"),
        event_ts=datetime(2026, 6, 9, 21, 0, tzinfo=UTC),
    )
    writer.insert_pdt_entry(
        trade_date=date(2026, 6, 10),
        event_type="eod_snapshot",
        day_trades_5d=2,
        settled_cash=Decimal("15000"),
        event_ts=datetime(2026, 6, 10, 21, 0, tzinfo=UTC),
    )
    latest = writer.conn.execute("SELECT day_trades_5d, settled_cash FROM v_pdt_latest").fetchone()
    assert latest == (2, Decimal("15000"))


def test_pdt_order_fk_checked(writer):
    with pytest.raises(LedgerWriteError, match="订单不存在"):
        writer.insert_pdt_entry(
            trade_date=date(2026, 6, 10),
            event_type="day_trade",
            day_trades_5d=1,
            settled_cash=Decimal("1"),
            order_id="ord_NOPE",
        )


def test_positions_and_account_snapshots(writer):
    writer.insert_position_snapshot(
        snapshot_date=date(2026, 6, 10),
        ticker="NVDA",
        qty=Decimal("10"),
        raw_text="NVDA 10 shares",
        snapshot_ts=datetime(2026, 6, 10, 20, 0, tzinfo=UTC),
    )
    writer.insert_position_snapshot(  # 同日重抓=新行,eod 视图取最新
        snapshot_date=date(2026, 6, 10),
        ticker="NVDA",
        qty=Decimal("12"),
        raw_text="NVDA 12 shares",
        snapshot_ts=datetime(2026, 6, 10, 21, 0, tzinfo=UTC),
    )
    eod = writer.conn.execute("SELECT qty FROM v_positions_eod").fetchone()[0]
    assert eod == Decimal("12")
    writer.insert_account_snapshot(snapshot_date=date(2026, 6, 10), total_equity=Decimal("99876.54"), raw_text="acct")
    assert writer.conn.execute("SELECT count(*) FROM account_daily").fetchone()[0] == 1


def test_agent_run_heartbeat_and_crash_evidence(writer):
    rid = writer.start_agent_run(kill_switch=False)
    # 未收尾=崩溃证据
    assert writer.conn.execute("SELECT finished_ts FROM agent_runs WHERE run_id = ?", [rid]).fetchone()[0] is None
    writer.finish_agent_run(run_id=rid, signals_seen=3, orders_placed=1, fills_scraped=2, export_ok=True)
    row = writer.conn.execute(
        "SELECT finished_ts, signals_seen, export_ok FROM agent_runs WHERE run_id = ?", [rid]
    ).fetchone()
    assert row[0] is not None
    assert row[1] == 3
    assert row[2] is True
    with pytest.raises(LedgerWriteError, match="已收尾"):
        writer.finish_agent_run(run_id=rid)


def test_watermark(writer):
    assert writer.current_watermark() is None
    writer.insert_watermark(last_seen_call_ts=CALL_TS, calls_seen=5)
    writer.insert_watermark(last_seen_call_ts=datetime(2026, 6, 10, 16, 0, tzinfo=UTC), calls_seen=0)
    assert writer.current_watermark() == datetime(2026, 6, 10, 16, 0, tzinfo=UTC)
