"""PaperBroker 测试:真价撮合 + 滑点 + 退出引擎(21d/止损/翻空)+ 持仓重建/快照。

价源用可注入假价(确定性),不碰 prices.db;ledger 用 tmp 库。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from src.execution.ledger import LedgerWriter, signal_id_for
from src.execution.paper_broker import PaperBroker, PaperBrokerConfig

CALL_TS = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)


class FakePrices:
    """{(ticker, iso_date): close} 查表;缺键 = 无价(None)。"""

    def __init__(self, table: dict[tuple[str, str], float]):
        self.table = table

    def close_on(self, ticker: str, d: date):
        v = self.table.get((ticker, d.isoformat()))
        return Decimal(str(v)) if v is not None else None


@pytest.fixture
def writer(tmp_path):
    w = LedgerWriter(tmp_path / "ledger.duckdb")
    yield w
    w.close()


def seed_signal(w, ticker="NVDA", handle="@cap", tweet="100"):
    sid = signal_id_for(tweet, ticker)
    w.insert_signal(
        signal_id=sid,
        tweet_id=tweet,
        handle=handle,
        tier="PROVEN",
        tier_csv_date=date(2026, 6, 1),
        ticker=ticker,
        direction="bullish",
        call_ts=CALL_TS,
        tweet_text="t",
        tweet_url="u",
        decision="followed",
        decision_reason="all_gates_passed",
        rule_version="v0.1",
    )
    return sid


# ── 入场 ─────────────────────────────────────────────────────────────────


def test_enter_fills_at_entry_close(writer):
    sid = seed_signal(writer)
    # call 2026-06-01(周一)→ entry_window T_entry = 06-02
    px = FakePrices({("NVDA", "2026-06-02"): 100.0})
    b = PaperBroker(writer, px, PaperBrokerConfig(per_order_usd=5000))
    oid = b.enter(signal_id=sid, handle="@cap", ticker="NVDA", call_ts=CALL_TS, rule_version="v0.1")
    assert oid is not None
    cur = writer.order_current(oid)
    assert cur["status"] == "filled"
    assert cur["side"] == "buy"
    assert cur["qty"] == Decimal("50")  # floor(5000/100)
    fill = writer.conn.execute("SELECT price FROM fills WHERE order_id = ?", [oid]).fetchone()
    assert fill[0] == Decimal("100.00")


def test_enter_slippage_raises_buy_price(writer):
    sid = seed_signal(writer)
    px = FakePrices({("NVDA", "2026-06-02"): 100.0})
    b = PaperBroker(writer, px, PaperBrokerConfig(per_order_usd=5000, slippage_bps=50))  # 0.5%
    oid = b.enter(signal_id=sid, handle="@cap", ticker="NVDA", call_ts=CALL_TS, rule_version="v0.1")
    price = writer.conn.execute("SELECT price FROM fills WHERE order_id = ?", [oid]).fetchone()[0]
    assert price == Decimal("100.50")  # 100 * 1.005
    assert writer.order_current(oid)["qty"] == Decimal("49")  # floor(5000/100.50)


def test_enter_skips_when_no_price(writer):
    sid = seed_signal(writer)
    b = PaperBroker(writer, FakePrices({}), PaperBrokerConfig())  # 入场日无价
    assert b.enter(signal_id=sid, handle="@cap", ticker="NVDA", call_ts=CALL_TS, rule_version="v0.1") is None
    assert writer.conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


def test_enter_skips_when_price_exceeds_budget(writer):
    sid = seed_signal(writer)
    px = FakePrices({("NVDA", "2026-06-02"): 6000.0})  # 单价 > 单仓预算
    b = PaperBroker(writer, px, PaperBrokerConfig(per_order_usd=5000))
    assert b.enter(signal_id=sid, handle="@cap", ticker="NVDA", call_ts=CALL_TS, rule_version="v0.1") is None


# ── 持仓重建 ─────────────────────────────────────────────────────────────


def test_open_lots_reconstructed_from_ledger(writer):
    sid = seed_signal(writer)
    px = FakePrices({("NVDA", "2026-06-02"): 100.0})
    b = PaperBroker(writer, px, PaperBrokerConfig(per_order_usd=5000))
    b.enter(signal_id=sid, handle="@cap", ticker="NVDA", call_ts=CALL_TS, rule_version="v0.1")
    lots = b.open_lots()
    assert len(lots) == 1
    assert lots[0].ticker == "NVDA"
    assert lots[0].qty == Decimal("50")
    assert lots[0].entry_date == date(2026, 6, 2)
    assert lots[0].entry_price == Decimal("100.00")


# ── 退出引擎 ─────────────────────────────────────────────────────────────


def _entered_broker(writer, prices, cfg):
    sid = seed_signal(writer)
    b = PaperBroker(writer, FakePrices(prices), cfg)
    b.enter(signal_id=sid, handle="@cap", ticker="NVDA", call_ts=CALL_TS, rule_version="v0.1")
    return b, sid


def test_exit_hold_21d(writer):
    # entry 06-02;21 个交易日后约 07-01。提前一天不退,到期退。
    b, sid = _entered_broker(
        writer,
        {("NVDA", "2026-06-02"): 100.0, ("NVDA", "2026-07-02"): 120.0},
        PaperBrokerConfig(per_order_usd=5000, hold_days=21),
    )
    # 06-15 远不到 21 个交易日
    assert b.forward_day(date(2026, 6, 15), rule_version="v0.1") == []
    exited = b.forward_day(date(2026, 7, 2), rule_version="v0.1")
    assert exited == [sid]
    sell = writer.conn.execute("SELECT exit_reason, side FROM v_orders_current WHERE side='sell'").fetchone()
    assert sell == ("hold_21d", "sell")


def test_exit_stop_loss(writer):
    b, sid = _entered_broker(
        writer,
        {("NVDA", "2026-06-02"): 100.0, ("NVDA", "2026-06-05"): 84.0},
        PaperBrokerConfig(per_order_usd=5000, stop_loss_pct=0.15),  # 跌破 85 止损
    )
    exited = b.forward_day(date(2026, 6, 5), rule_version="v0.1")
    assert exited == [sid]
    assert (
        writer.conn.execute("SELECT exit_reason FROM v_orders_current WHERE side='sell'").fetchone()[0] == "stop_loss"
    )


def test_stop_loss_not_triggered_above_floor(writer):
    b, sid = _entered_broker(
        writer,
        {("NVDA", "2026-06-02"): 100.0, ("NVDA", "2026-06-05"): 86.0},
        PaperBrokerConfig(per_order_usd=5000, stop_loss_pct=0.15),
    )
    assert b.forward_day(date(2026, 6, 5), rule_version="v0.1") == []


def test_exit_direction_flip(writer):
    b, sid = _entered_broker(
        writer, {("NVDA", "2026-06-02"): 100.0, ("NVDA", "2026-06-10"): 110.0}, PaperBrokerConfig(per_order_usd=5000)
    )
    flips = {("@cap", "NVDA"): date(2026, 6, 9)}
    exited = b.forward_day(date(2026, 6, 10), rule_version="v0.1", flips=flips)
    assert exited == [sid]
    assert (
        writer.conn.execute("SELECT exit_reason FROM v_orders_current WHERE side='sell'").fetchone()[0]
        == "direction_flip"
    )


def test_exit_priority_flip_over_hold(writer):
    # 同时满足翻空与到期,翻空优先
    b, sid = _entered_broker(
        writer,
        {("NVDA", "2026-06-02"): 100.0, ("NVDA", "2026-07-02"): 120.0},
        PaperBrokerConfig(per_order_usd=5000, hold_days=21),
    )
    flips = {("@cap", "NVDA"): date(2026, 7, 1)}
    b.forward_day(date(2026, 7, 2), rule_version="v0.1", flips=flips)
    assert (
        writer.conn.execute("SELECT exit_reason FROM v_orders_current WHERE side='sell'").fetchone()[0]
        == "direction_flip"
    )


def test_exit_skips_when_no_price_that_day(writer):
    b, sid = _entered_broker(
        writer, {("NVDA", "2026-06-02"): 100.0}, PaperBrokerConfig(per_order_usd=5000, hold_days=21)
    )
    # 07-01 无价 → 不强平
    assert b.forward_day(date(2026, 7, 1), rule_version="v0.1") == []
    assert writer.conn.execute("SELECT count(*) FROM v_orders_current WHERE side='sell'").fetchone()[0] == 0


def test_exited_lot_no_longer_open(writer):
    b, sid = _entered_broker(
        writer,
        {("NVDA", "2026-06-02"): 100.0, ("NVDA", "2026-07-02"): 120.0},
        PaperBrokerConfig(per_order_usd=5000, hold_days=21),
    )
    b.forward_day(date(2026, 7, 2), rule_version="v0.1")
    assert b.open_lots() == []  # 平仓后不再是未平仓单
    # 二次 forward 不重复卖
    assert b.forward_day(date(2026, 7, 2), rule_version="v0.1") == []


# ── 持仓快照 ─────────────────────────────────────────────────────────────


def test_snapshot_positions_with_unrealized(writer):
    b, sid = _entered_broker(
        writer, {("NVDA", "2026-06-02"): 100.0, ("NVDA", "2026-06-10"): 110.0}, PaperBrokerConfig(per_order_usd=5000)
    )
    n = b.snapshot_positions(date(2026, 6, 10))
    assert n == 1
    row = writer.conn.execute("SELECT qty, avg_cost, close, unrealized_pnl FROM positions_daily").fetchone()
    assert row[0] == Decimal("50") and row[1] == Decimal("100.00")
    assert row[2] == Decimal("110.0")
    assert row[3] == Decimal("500.00")  # (110-100)*50


def test_recon_ledger_qty_zero_after_round_trip(writer):
    b, sid = _entered_broker(
        writer,
        {("NVDA", "2026-06-02"): 100.0, ("NVDA", "2026-07-02"): 120.0},
        PaperBrokerConfig(per_order_usd=5000, hold_days=21),
    )
    b.forward_day(date(2026, 7, 2), rule_version="v0.1")
    # 买 50 卖 50 → 净持仓 0(A 组对账口径)
    net = writer.conn.execute(
        "SELECT coalesce(sum(ledger_qty),0) FROM v_recon_ledger_qty WHERE ticker='NVDA'"
    ).fetchone()[0]
    assert net == Decimal("0")
