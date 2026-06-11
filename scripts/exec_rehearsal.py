"""端到端演练 runner(P1 收尾 / ROADMAP P1 通过标准;P2 operator 首登的前置门)。

链路:真实信号(Data 管线 signal_snapshots.duckdb 近 7 天候选,**拷贝后使用**,
红线 1 不碰主树原件)→ 规则引擎 src/rules(prices 注入假价,P2 前无权威价源)
→ ledger 下单(fake submit→fake fill,**绝不碰浏览器**)→ fills 落账(含 §5.3
作废对 + 更正行演练)→ 每循环 parquet 原子导出 → EOD 快照 + 对账。

验收四问(Lead 指令):
  ① 审计五问 SQL(spec §8 原文)对任一假单一条答全;
  ② Dash reader 检测到导出自动脱离 MOCK(export_available → 真实现);
  ③ Valid 对账 A 组不变量(RECON_DESIGN_V0 §2,字典 owner=%Valid)全过;
  ④ agent_runs 心跳完整(每轮 started/finished/export_ok)。

用法:uv run python scripts/exec_rehearsal.py
退出码 0 = 四问全过。产物:data/execution/rehearsal/(演练库)+
data/execution/export/(契约导出,Dash 即刻可读)。均 gitignored。
"""

from __future__ import annotations

import math
import shutil
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import polars as pl
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.execution.ledger import LedgerWriter, export_ledger  # noqa: E402
from src.rules.engine import PdtState, RuleParams  # noqa: E402
from src.rules.runner import run_decision_cycle  # noqa: E402

# 真实信号源(Data 管线产物,主树;只作拷贝源,绝不写)
SOURCE_SIGNALS_DB = Path.home() / "quant-stock" / "data" / "signals" / "signal_snapshots.duckdb"

REHEARSAL_DIR = PROJECT_ROOT / "data" / "execution" / "rehearsal"
LEDGER_DB = REHEARSAL_DIR / "ledger.duckdb"
SIGNALS_DB_COPY = REHEARSAL_DIR / "signal_snapshots.duckdb"
EXPORT_DIR = PROJECT_ROOT / "data" / "execution" / "export"  # 契约默认目录(Dash reader 同源推导)

INITIAL_SETTLED_CASH = Decimal("100000.00")  # 演练假设;真实额度待 P2 首登核验(诚实声明在案)
PARAMS = RuleParams()  # v0.1 默认,per_order_usd=$5k

_FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    logger.log("SUCCESS" if ok else "ERROR", "{} {} {}", "PASS" if ok else "FAIL", name, detail)
    if not ok:
        _FAILURES.append(f"{name}: {detail}")


def fake_price(ticker: str) -> float:
    """确定性注入价($5–$500),P2 前无权威价源——不假装有,价是假的但确定可复现。"""
    h = sum((i + 1) * ord(c) for i, c in enumerate(ticker))
    return round(5.0 + (h * 7919 % 49500) / 100.0, 2)


def setup() -> None:
    if not SOURCE_SIGNALS_DB.exists():
        raise SystemExit(f"信号源不存在(先跑 Data 管线): {SOURCE_SIGNALS_DB}")
    if REHEARSAL_DIR.exists():
        shutil.rmtree(REHEARSAL_DIR)  # 演练可重跑:只清演练自己的目录
    REHEARSAL_DIR.mkdir(parents=True)
    shutil.copy2(SOURCE_SIGNALS_DB, SIGNALS_DB_COPY)
    logger.info("信号库已拷贝(主树原件只读不碰): {}", SIGNALS_DB_COPY)


def load_candidates() -> pl.DataFrame:
    con = duckdb.connect(str(SIGNALS_DB_COPY), read_only=True)
    try:
        return con.execute("SELECT * FROM signal_candidates").pl()
    finally:
        con.close()


def load_decisions() -> pl.DataFrame:
    con = duckdb.connect(str(SIGNALS_DB_COPY), read_only=True)
    try:
        return con.execute("SELECT * FROM rule_decisions").pl()
    finally:
        con.close()


# ── Round 1:收录 + 决策 + fake submit ───────────────────────────────────


def round1_ingest_and_submit(w: LedgerWriter) -> tuple[list[str], Decimal]:
    now = datetime.now(UTC)
    run_id = w.start_agent_run(kill_switch=False, note="REHEARSAL round1: ingest+decide+fake submit")
    cands = load_candidates()
    prices = {t: fake_price(t) for t in cands["ticker"].unique()}
    stats = run_decision_cycle(
        prices=prices,
        pdt=PdtState(day_trades_5d=0, settled_cash=float(INITIAL_SETTLED_CASH)),
        db_path=SIGNALS_DB_COPY,
        decision_ts=now,
        params=PARAMS,
    )
    logger.info("规则引擎: {}", stats)

    decided = cands.join(load_decisions(), on="signal_id", how="inner")
    for r in decided.to_dicts():
        w.insert_signal(
            signal_id=r["signal_id"],
            tweet_id=r["tweet_id"],
            handle=r["handle"],
            author_id=r["author_id"],
            tier=r["tier"],
            tier_csv_date=r["tier_csv_date"],
            ticker=r["ticker"],
            direction=r["direction"],
            call_ts=r["call_ts"],
            ingested_ts=r["ingested_ts"],
            tweet_text=r["tweet_text"],
            tweet_url=r["tweet_url"],
            tweet_created_at=r["tweet_created_at"],
            tweet_blocked=r["tweet_blocked"],
            conviction=r["conviction"],
            confidence=r["confidence"],
            decision=r["decision"],
            decision_reason=r["decision_reason"],
            rule_version=r["rule_version"],
        )

    # B4 簿记起点(演练假设 $100k,真实额度待 P2 核验)
    settled = INITIAL_SETTLED_CASH
    w.insert_pdt_entry(
        trade_date=now.date(),
        event_type="eod_snapshot",
        day_trades_5d=0,
        settled_cash=settled,
        note="REHEARSAL 初始簿记(金额为演练假设)",
    )

    order_ids: list[str] = []
    followed = decided.filter(pl.col("decision") == "followed")
    for r in followed.to_dicts():
        price = Decimal(str(prices[r["ticker"]]))
        qty = Decimal(math.floor(PARAMS.per_order_usd / float(price)))
        submitted = datetime.now(UTC)
        oid = w.open_order(
            signal_id=r["signal_id"],
            ticker=r["ticker"],
            side="buy",
            qty=qty,
            order_type="limit",
            limit_price=price,
            submitted_ts=submitted,
            call_to_submit_ms=int((submitted - r["call_ts"]).total_seconds() * 1000),
            rule_version=r["rule_version"],
            kill_switch_engaged=False,
            note="REHEARSAL fake submit(未触达浏览器); confirmation_unverified",
        )
        cost = (qty * price).quantize(Decimal("0.01"))
        settled -= cost
        w.insert_pdt_entry(
            trade_date=submitted.date(),
            event_type="cash_debit",
            ticker=r["ticker"],
            order_id=oid,
            cash_delta=-cost,
            settle_date=submitted.date(),
            day_trades_5d=0,
            settled_cash=settled,
            note="REHEARSAL",
        )
        order_ids.append(oid)

    w.insert_watermark(last_seen_call_ts=cands["call_ts"].max(), calls_seen=cands.height, note="REHEARSAL round1")
    ok = export_ledger(w.conn, EXPORT_DIR)
    w.finish_agent_run(
        run_id=run_id,
        signals_seen=decided.height,
        orders_placed=len(order_ids),
        export_ok=ok,
    )
    logger.info("round1: 收录 {} / followed 下单 {}", decided.height, len(order_ids))
    return order_ids, settled


# ── Round 2:fake fill 回采(含部分成交 / 作废对 / 更正行演练)────────────


def round2_fake_fills(w: LedgerWriter, order_ids: list[str]) -> int:
    run_id = w.start_agent_run(kill_switch=False, note="REHEARSAL round2: fake fill 回采")
    fills = 0
    for i, oid in enumerate(order_ids):
        cur = w.order_current(oid)
        qty, price = cur["qty"], cur["limit_price"]
        ref = f"FT-{i:06d}"
        ts = datetime.now(UTC)
        raw = f"Order {ref}: {cur['ticker']} BUY {qty} @ {price} FILLED (paper)"

        if i == 0 and qty >= 2:  # 演练 partial→filled 两段成交
            q1 = Decimal(int(qty) // 2)
            w.append_order_event(order_id=oid, status="partial", broker_order_ref=ref)
            w.insert_fill(order_id=oid, fill_ts=ts, qty=q1, price=price, raw_text=raw)
            w.append_order_event(order_id=oid, status="filled")
            w.insert_fill(order_id=oid, fill_ts=ts + timedelta(seconds=30), qty=qty - q1, price=price, raw_text=raw)
            fills += 2
        elif i == 1:  # 演练 fills 作废对(§5.3):错价 → void → 正确行
            w.append_order_event(order_id=oid, status="filled", broker_order_ref=ref)
            wrong = price + Decimal("1.00")
            bad = w.insert_fill(order_id=oid, fill_ts=ts, qty=qty, price=wrong, raw_text=f"REHEARSAL 解析错价 {wrong}")
            w.void_fill(fill_id=bad, note="REHEARSAL 演练:价格解析错误,作废重记")
            w.insert_fill(order_id=oid, fill_ts=ts, qty=qty, price=price, raw_text=raw)
            fills += 1
        elif i == 2:  # 演练 orders 更正行(§5.3 终态豁免):误记 filled → 更正 → expired
            w.append_order_event(order_id=oid, status="filled", broker_order_ref=ref, note="REHEARSAL 误记(演练剧本)")
            w.append_order_event(
                order_id=oid,
                status="submitted",
                corrects_seq=1,
                note="REHEARSAL 演练:回采误记 filled,实际未成交,更正回 submitted",
            )
            w.append_order_event(order_id=oid, status="expired", note="REHEARSAL 日内限价收盘未成交")
        else:  # 常规一次全成
            w.append_order_event(order_id=oid, status="filled", broker_order_ref=ref)
            w.insert_fill(order_id=oid, fill_ts=ts, qty=qty, price=price, raw_text=raw)
            fills += 1
    ok = export_ledger(w.conn, EXPORT_DIR)
    w.finish_agent_run(run_id=run_id, fills_scraped=fills, export_ok=ok)
    logger.info("round2: 回采 fills {}(含作废对 1 + 更正行 1)", fills)
    return fills


# ── Round 3:EOD 快照 + §7 对账 ──────────────────────────────────────────


def round3_eod(w: LedgerWriter, settled: Decimal) -> None:
    run_id = w.start_agent_run(kill_switch=False, note="REHEARSAL round3: EOD 快照+对账")
    today = datetime.now(UTC).date()
    pos = w.conn.execute(
        """
        SELECT r.ticker, r.ledger_qty,
               f.cost / nullif(r.ledger_qty, 0) AS avg_cost
        FROM v_recon_ledger_qty r
        LEFT JOIN (SELECT o.ticker, sum(f.qty * f.price) AS cost
                   FROM v_fills_effective f JOIN v_orders_current o USING (order_id)
                   WHERE o.side = 'buy' GROUP BY o.ticker) f USING (ticker)
        WHERE r.ledger_qty > 0
        """
    ).fetchall()
    equity = settled
    for ticker, qty, avg_cost in pos:
        px = Decimal(str(fake_price(ticker)))
        w.insert_position_snapshot(
            snapshot_date=today,
            ticker=ticker,
            qty=qty,
            avg_cost=Decimal(str(round(avg_cost, 6))) if avg_cost is not None else None,
            close=px,
            raw_text=f"REHEARSAL positions page: {ticker} {qty} @ {avg_cost}",
        )
        equity += (Decimal(qty) * px).quantize(Decimal("0.01"))
    w.insert_account_snapshot(
        snapshot_date=today,
        total_equity=equity,
        cash=settled,
        raw_text="REHEARSAL account page",
    )
    # §7:fills 累计 vs positions_daily(演练中后者源于前者,属管线冒烟而非独立核对——报告注明)
    mismatch = w.conn.execute(
        """
        SELECT count(*) FROM v_recon_ledger_qty r
        FULL OUTER JOIN (SELECT ticker, qty FROM v_positions_eod WHERE snapshot_date = ?) p
            USING (ticker)
        WHERE coalesce(r.ledger_qty, 0) <> coalesce(p.qty, 0) AND coalesce(r.ledger_qty, 0) > 0
        """,
        [today],
    ).fetchone()[0]
    if mismatch == 0:
        w.insert_pdt_entry(
            trade_date=today,
            event_type="eod_snapshot",
            day_trades_5d=0,
            settled_cash=settled,
            note="recon=ok (REHEARSAL)",
        )
    ok = export_ledger(w.conn, EXPORT_DIR)
    w.finish_agent_run(run_id=run_id, export_ok=ok)
    logger.info("round3: 持仓快照 {} 票 / recon mismatch {} / 权益 {}", len(pos), mismatch, equity)


# ── 验收 ① 审计五问(spec §8 SQL 原文)──────────────────────────────────


def accept_1_audit_sql(w: LedgerWriter) -> None:
    oid = w.conn.execute("SELECT order_id FROM v_orders_current WHERE status = 'filled' LIMIT 1").fetchone()[0]
    row = w.conn.execute(
        """
        SELECT
            s.call_ts,
            o.submitted_ts, o.call_to_submit_ms,
            s.handle, s.tweet_url, s.tweet_text,
            s.tier, s.tier_csv_date,
            s.decision, s.decision_reason, o.rule_version,
            f.filled_qty, f.avg_fill_price,
            o.status, o.exit_reason,
            o.kill_switch_engaged
        FROM v_orders_current o
        JOIN signals s USING (signal_id)
        LEFT JOIN v_order_filled f USING (order_id)
        WHERE o.order_id = ?
        """,
        [oid],
    ).fetchone()
    # 五问字段逐项非空(exit_reason 是开仓单,允许 NULL)
    answered = all(
        v is not None
        for k, v in zip(
            (
                "call_ts",
                "submitted_ts",
                "call_to_submit_ms",
                "handle",
                "tweet_url",
                "tweet_text",
                "tier",
                "tier_csv_date",
                "decision",
                "decision_reason",
                "rule_version",
                "filled_qty",
                "avg_fill_price",
                "status",
                "kill_switch_engaged",
            ),
            (*row[:14], row[15]),
            strict=True,
        )
    )
    check("①审计五问 SQL", answered, f"order={oid} 一条 SQL 答全(spec §8 原文)")
    logger.info(
        "审计样例: 跟谁={} 何时喊={} 下单延迟={}ms 成交={}@{} 状态={}",
        row[3],
        row[0],
        row[2],
        row[11],
        round(float(row[12]), 4),
        row[13],
    )


# ── 验收 ② Dash 脱 MOCK ────────────────────────────────────────────────


def accept_2_dash_leaves_mock() -> None:
    from dashboard import ledger_mock, ledger_reader  # noqa: PLC0415 — 导出就绪后才 import

    available = ledger_reader.export_available()
    lm = ledger_reader if available else ledger_mock  # 页面同款选择逻辑(11/12 页)
    state, age_min, _ts = ledger_reader.freshness()
    orders = ledger_reader.load_orders_current()
    sigs = ledger_reader.load_signals()
    ok = available and lm.IS_MOCK is False and state == "fresh" and len(orders) > 0 and len(sigs) > 0
    check(
        "②Dash 脱 MOCK",
        ok,
        f"export_available={available} 选中={'reader' if not lm.IS_MOCK else 'mock'} "
        f"freshness={state}({age_min and round(age_min, 1)}min) orders={len(orders)} signals={len(sigs)}",
    )


# ── 验收 ③ 对账不变量 A 组(RECON_DESIGN_V0 §2,字典 owner=%Valid)──────


def accept_3_recon_invariants(w: LedgerWriter) -> None:
    c = w.conn

    def q1(sql: str, params: list | None = None) -> int:
        return c.execute(sql, params or []).fetchone()[0]

    a1 = q1("""SELECT count(*) FROM v_fills_effective f
               WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.order_id = f.order_id)""")
    check("③A1 无孤儿成交", a1 == 0, f"违反 {a1}")

    a2 = q1("""SELECT count(*) FROM v_order_filled f JOIN v_orders_current o USING (order_id)
               WHERE f.filled_qty > o.qty""")
    check("③A2 无超额成交", a2 == 0, f"违反 {a2}")

    a3 = q1("""
        SELECT count(*) FROM v_orders_current o
        LEFT JOIN v_order_filled f USING (order_id)
        WHERE (o.status = 'filled'   AND coalesce(f.filled_qty, 0) <> o.qty)
           OR (o.status = 'partial'  AND NOT (coalesce(f.filled_qty, 0) > 0
                                              AND f.filled_qty < o.qty))
           OR (o.status = 'rejected' AND coalesce(f.filled_qty, 0) > 0)
           OR (o.status = 'cancelled' AND coalesce(f.filled_qty, 0) > 0
               AND NOT EXISTS (SELECT 1 FROM orders h
                               WHERE h.order_id = o.order_id AND h.status = 'partial'))
        """)
    check("③A3 状态↔成交一致", a3 == 0, f"违反 {a3}")

    a4 = q1("""SELECT count(*) FROM v_orders_current o JOIN v_order_filled f USING (order_id)
               WHERE o.status = 'submitted'""")
    check("③A4 无滞留 submitted-有成交", a4 == 0, f"违反 {a4}(演练同轮推进,时滞=0)")

    a5_gap = q1("""SELECT count(*) FROM (
                     SELECT order_id, count(*) AS c, min(seq) AS mn, max(seq) AS mx
                     FROM orders GROUP BY order_id) WHERE mn <> 0 OR mx <> c - 1""")
    a5_term = q1("""SELECT count(*) FROM (
                      SELECT corrects_seq,
                             lag(status) OVER (PARTITION BY order_id ORDER BY seq) AS prev
                      FROM orders)
                    WHERE prev IN ('filled','cancelled','rejected','expired')
                      AND corrects_seq IS NULL""")
    check(
        "③A5 事件流完整",
        a5_gap == 0 and a5_term == 0,
        f"seq 缺口 {a5_gap} / 终态后非更正行 {a5_term}(r3:更正行豁免,字典待 Valid 同步)",
    )

    a6 = q1("""
        SELECT count(*) FROM v_orders_current o LEFT JOIN signals s USING (signal_id)
        WHERE s.signal_id IS NULL
           OR (o.side = 'buy' AND s.decision <> 'followed')
           OR (o.side = 'sell' AND o.exit_reason IS NULL)
           OR (o.exit_reason = 'direction_flip' AND o.exit_trigger_signal_id IS NULL)
        """)
    check("③A6 关联链完整", a6 == 0, f"违反 {a6}")

    a7 = q1("""
        SELECT count(*) FROM orders o JOIN signals s USING (signal_id)
        WHERE o.seq = 0 AND NOT (s.call_ts <= s.ingested_ts AND s.ingested_ts <= o.submitted_ts)
        """) + q1("""
        SELECT count(*) FROM v_fills_effective f
        JOIN (SELECT order_id, submitted_ts FROM orders WHERE seq = 0) o USING (order_id)
        WHERE f.fill_ts < o.submitted_ts - INTERVAL 5 MINUTE
        """)
    check("③A7 时间线单调", a7 == 0, f"违反 {a7}")

    a8 = q1("""
        SELECT count(*) FROM (
          SELECT o.ticker,
                 sum(CASE WHEN o.side = 'sell' THEN f.qty ELSE 0 END) AS sold,
                 sum(CASE WHEN o.side = 'buy'  THEN f.qty ELSE 0 END) AS bought
          FROM v_fills_effective f JOIN v_orders_current o USING (order_id)
          GROUP BY o.ticker) WHERE sold > bought
        """)
    check("③A8 卖不超持", a8 == 0, f"违反 {a8}(演练无平仓腿,弱口径=总量)")

    age_h = c.execute("SELECT date_diff('minute', max(poll_ts), now()) FROM ingest_watermark").fetchone()[0]
    check("③A9 水位新鲜", age_h is not None and age_h <= 120, f"距上轮 {age_h}min(阈值 120)")


# ── 验收 ④ agent_runs 心跳 ─────────────────────────────────────────────


def accept_4_heartbeats(w: LedgerWriter) -> None:
    rows = w.conn.execute(
        "SELECT run_id, finished_ts IS NOT NULL, coalesce(export_ok, FALSE), error FROM agent_runs ORDER BY started_ts"
    ).fetchall()
    ok = len(rows) == 3 and all(fin and exp and err is None for _, fin, exp, err in rows)
    check("④agent_runs 心跳完整", ok, f"{len(rows)} 轮,finished/export_ok 全真,error 全空" if ok else f"rows={rows}")


def main() -> int:
    logger.info("=== 端到端演练开始(fake submit/fill,绝不碰浏览器)===")
    setup()
    with LedgerWriter(LEDGER_DB) as w:
        order_ids, settled = round1_ingest_and_submit(w)
        if not order_ids:
            check("前置:存在 followed 订单", False, "规则引擎 0 followed,演练无法覆盖下单链")
        else:
            round2_fake_fills(w, order_ids)
            round3_eod(w, settled)
            accept_1_audit_sql(w)
            accept_2_dash_leaves_mock()
            accept_3_recon_invariants(w)
            accept_4_heartbeats(w)

    if _FAILURES:
        logger.error("演练 FAIL({} 项): {}", len(_FAILURES), _FAILURES)
        return 1
    logger.success("演练四问全过。导出: {}(Dash 现在即可脱 MOCK 预览)", EXPORT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
