"""①回填种子跑(P2 第一步,给 operator 第一个真结果)。

Data 管线已产 ~7 天 71 个真实候选 → 按各自入场交易日的**真实历史收盘价**时序回放:
每个交易日 D = 规则引擎决策(以 D 为决策时点,真价做门)→ followed 按 D 收盘建仓 →
退出引擎按 D 收盘评估(21d/止损/翻空)→ 导出。跑到最新交易日,多数仓因 21d 未满
仍开着,标到最新收盘的未实现盈亏。**诚实标注:观察期早期,绝大多数未实现。**

按日时序回放(非 decision_ts=now)的理由:候选 call 散在近 7 天,若一律以"今天"决策,
旧 call 会被 signal_stale 误杀;以各自入场日决策才还原"当时会不会跟、跟了什么价"。

用法:uv run python scripts/paper_backfill.py
产物:data/execution/rehearsal/(回放库)+ data/execution/export/(Dash 可读)+
docs/PAPER_BACKFILL_REPORT.md。均 gitignored(报告除外)。
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.execution.ledger import LedgerWriter, export_ledger  # noqa: E402
from src.execution.paper_broker import PaperBroker, PaperBrokerConfig, PricesDbClose  # noqa: E402
from src.rules.engine import PdtState, PortfolioState, Position, RuleParams, decide, entry_window  # noqa: E402
from src.rules.runner import fetch_recent_bearish  # noqa: E402
from src.signals.honest_leaderboard import proven  # noqa: E402

_ET = ZoneInfo("America/New_York")
SOURCE_SIGNALS_DB = Path.home() / "quant-stock" / "data" / "signals" / "signal_snapshots.duckdb"
PRICES_DB = Path.home() / ".stock-picker-mcp" / "prices.db"
REHEARSAL_DIR = PROJECT_ROOT / "data" / "execution" / "rehearsal"
LEDGER_DB = REHEARSAL_DIR / "ledger.duckdb"
SIGNALS_COPY = REHEARSAL_DIR / "signal_snapshots.duckdb"
EXPORT_DIR = PROJECT_ROOT / "data" / "execution" / "export"
REPORT = PROJECT_ROOT / "docs" / "PAPER_BACKFILL_REPORT.md"

# 演练假设(本地模拟;真钱 $300 路线 P3 另算):$100k 起始、单仓 $5k、滑点 0 起步
INITIAL_CASH = Decimal("100000.00")
PARAMS = RuleParams()
BROKER_CFG = PaperBrokerConfig(per_order_usd=PARAMS.per_order_usd, slippage_bps=0.0, hold_days=21)
RULE_VERSION = PARAMS.rule_version


def setup() -> None:
    if not SOURCE_SIGNALS_DB.exists():
        raise SystemExit(f"信号源不存在: {SOURCE_SIGNALS_DB}")
    if not PRICES_DB.exists():
        raise SystemExit(f"价源不存在: {PRICES_DB}")
    if REHEARSAL_DIR.exists():
        shutil.rmtree(REHEARSAL_DIR)
    REHEARSAL_DIR.mkdir(parents=True)
    shutil.copy2(SOURCE_SIGNALS_DB, SIGNALS_COPY)  # 主树原件只读,拷贝后用(红线 1)


def load_candidates() -> pl.DataFrame:
    con = duckdb.connect(str(SIGNALS_COPY), read_only=True)
    try:
        return con.execute("SELECT * FROM signal_candidates").pl()
    finally:
        con.close()


def _decision_ts(d: date) -> datetime:
    return datetime.combine(d, time(15, 30), tzinfo=_ET)  # 15:30 ET 决策点(spec §0)


def replay(w: LedgerWriter, broker: PaperBroker, cands: pl.DataFrame) -> dict:
    px = PricesDbClose(PRICES_DB)
    board = proven("21d")
    handle_wilson = {h: wl for h, wl in zip(board["handle"], board["wilson_lo"], strict=True) if wl is not None}

    # 每候选算入场日,按入场日分组时序回放
    with_entry = cands.with_columns(
        pl.col("call_ts").map_elements(lambda t: entry_window(t)[0], return_dtype=pl.Date).alias("entry_date")
    )
    today = datetime.now(UTC).astimezone(_ET).date()
    entry_dates = sorted(d for d in with_entry["entry_date"].unique().to_list() if d <= today)
    logger.info("回放窗口: {} ~ {}({} 个入场日)", entry_dates[0], today, len(entry_dates))

    inserted_signals = 0
    followed_total = 0
    for d in entry_dates:
        cohort = with_entry.filter(pl.col("entry_date") == d).drop("entry_date")
        # 决策门用 D 当日真价
        prices = {t: float(px.close_on(t, d)) for t in cohort["ticker"].unique().to_list() if px.close_on(t, d) is not None}
        # 组合状态从 ledger 现状重建(已开仓/挂单占槽)
        lots = broker.open_lots()
        portfolio = PortfolioState(open_positions=tuple(Position(ticker=lt.ticker, handle=lt.handle) for lt in lots))
        bearish = fetch_recent_bearish(_decision_ts(d), set(handle_wilson))
        decisions = decide(
            cohort, decision_ts=_decision_ts(d), pdt=PdtState(day_trades_5d=0, settled_cash=float(INITIAL_CASH)),
            prices=prices, portfolio=portfolio, handle_wilson=handle_wilson, recent_bearish=bearish, params=PARAMS,
        )
        decided = cohort.join(decisions, on="signal_id", how="inner")
        for r in decided.to_dicts():
            if w.insert_signal(
                signal_id=r["signal_id"], tweet_id=r["tweet_id"], handle=r["handle"], author_id=r["author_id"],
                tier=r["tier"], tier_csv_date=r["tier_csv_date"], ticker=r["ticker"], direction=r["direction"],
                call_ts=r["call_ts"], ingested_ts=r["ingested_ts"], tweet_text=r["tweet_text"],
                tweet_url=r["tweet_url"], tweet_created_at=r["tweet_created_at"], tweet_blocked=r["tweet_blocked"],
                conviction=r["conviction"], confidence=r["confidence"], decision=r["decision"],
                decision_reason=r["decision_reason"], rule_version=r["rule_version"],
            ):
                inserted_signals += 1
        for r in decided.filter(pl.col("decision") == "followed").to_dicts():
            oid = broker.enter(
                signal_id=r["signal_id"], handle=r["handle"], ticker=r["ticker"],
                call_ts=r["call_ts"], rule_version=RULE_VERSION, entry_date=d,
            )
            if oid:
                followed_total += 1
        # 当日退出评估(翻空用当日 bearish 集)
        flips = {(b["handle"], b["ticker"]): _decision_ts(d).date() for b in bearish.to_dicts()}
        broker.forward_day(d, rule_version=RULE_VERSION, flips=flips)
        export_ledger(w.conn, EXPORT_DIR)

    return {"entry_dates": entry_dates, "today": today, "signals": inserted_signals, "followed": followed_total}


def finalize_and_report(w: LedgerWriter, broker: PaperBroker, meta: dict) -> None:
    px = PricesDbClose(PRICES_DB)
    today = meta["today"]
    n_pos = broker.snapshot_positions(today)
    # 账户权益 = 现金余 + 持仓市值;现金 = 初始 - 净买入成本(粗口径,演练)
    open_lots = broker.open_lots()
    invested = sum((lt.entry_price * lt.qty for lt in open_lots), Decimal("0"))
    mkt_val = Decimal("0")
    unreal = Decimal("0")
    rows = []
    for lt in open_lots:
        close = px.close_on(lt.ticker, today)
        mv = (close * lt.qty) if close is not None else (lt.entry_price * lt.qty)
        pnl = ((close - lt.entry_price) * lt.qty) if close is not None else Decimal("0")
        mkt_val += mv
        unreal += pnl
        rows.append((lt.handle, lt.ticker, lt.entry_date, lt.qty, lt.entry_price, close, pnl))
    cash = INITIAL_CASH - invested
    equity = cash + mkt_val
    w.insert_account_snapshot(snapshot_date=today, total_equity=equity, cash=cash, raw_text="PAPER backfill seed")
    export_ledger(w.conn, EXPORT_DIR)

    # ledger 统计
    closed = w.conn.execute("SELECT count(*) FROM v_orders_current WHERE side='sell'").fetchone()[0]
    by_reason = w.conn.execute(
        "SELECT decision_reason, count(*) FROM signals GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    handles = w.conn.execute("SELECT DISTINCT handle FROM signals WHERE decision='followed'").fetchall()
    rows.sort(key=lambda x: x[6], reverse=True)

    lines = [
        "# PAPER_BACKFILL_REPORT — 首份本地模拟盘种子结果(观察期早期)",
        "",
        f"> %Exec,{today}。脚本 `scripts/paper_backfill.py`(可重跑)。",
        "> 链路:71 真实候选 → 规则引擎按各入场日时序决策 → PaperBroker 按**真实历史收盘价**建仓 → 退出引擎。",
        "> **观察期早期诚实声明:候选集中在近 7 天,21d 持有未满,绝大多数仓仍开着,下表是标到最新收盘的"
        "未实现盈亏,不是已实现收益,样本与时长都远不足以判 edge。**",
        "",
        "## 跑了什么",
        "",
        f"- 候选 71 → 收录决策 {meta['signals']} 条;**followed 建仓 {meta['followed']} 笔**,已平仓 {closed} 笔"
        f"(多数因 21d 未满仍持有)。",
        f"- 跟单博主(followed 去重):{', '.join(sorted(h[0] for h in handles)) or '—'}。",
        f"- 回放窗口:{meta['entry_dates'][0]} ~ {today}(按入场交易日逐日回放)。",
        f"- 参数(演练假设,非真钱):起始 ${INITIAL_CASH:,.0f}、单仓 ${PARAMS.per_order_usd:,.0f}、滑点 "
        f"{BROKER_CFG.slippage_bps}bps、持有 {BROKER_CFG.hold_days} 交易日、止损"
        f"{'未启用(阈值待 rule_version 定,不臆造)' if BROKER_CFG.stop_loss_pct is None else f'{BROKER_CFG.stop_loss_pct:.0%}'}。",
        "",
        "## 决策分布(规则引擎门序真实触发)",
        "",
        "| decision_reason | 数量 |",
        "|---|---|",
        *[f"| {r[0]} | {r[1]} |" for r in by_reason],
        "",
        "## 当前持仓未实现盈亏(标到最新收盘,观察期早期)",
        "",
        "| 博主 | ticker | 入场日 | 股数 | 入场价 | 最新收盘 | 未实现 $ |",
        "|---|---|---|---|---|---|---|",
        *[
            f"| {h} | {tk} | {ed} | {q} | {ep} | {c if c is not None else 'N/A'} | {pnl:+.2f} |"
            for h, tk, ed, q, ep, c, pnl in rows
        ],
        "",
        "## 账户(演练口径,粗算)",
        "",
        f"- 投入成本 ${invested:,.2f} / 持仓市值 ${mkt_val:,.2f} / 现金余 ${cash:,.2f}",
        f"- **总权益 ${equity:,.2f}**(起始 ${INITIAL_CASH:,.0f},未实现 {unreal:+.2f})",
        "",
        "## 诚实边界(必读)",
        "",
        "1. 成交价 = 入场/评估日**日收盘**,非盘中真实成交价(prices.db 无 tick/OHLCV);滑点 0 是乐观假设。",
        "2. 这是**本地账本模拟**,无券商、无真金;E 账=本地成交,绝不等同未来真实账户。",
        "3. **观察期早期**:21d 多数未满,未实现盈亏会随后续行情大幅变动,**不可据此判 edge**。",
        "4. 现金/权益为粗口径(未计股息/费用/未结算);精确 S账/E账/S−E 归因归 %Valid。",
        "5. 价源新鲜度由 Data 保证(前向每日跑依赖其更新到最新交易日);本回填用截至 "
        f"{today} 的 price_cache。",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.success("回填完成: followed {} / 平仓 {} / 持仓 {} / 权益 {} / 报告 {}",
                   meta["followed"], closed, n_pos, equity, REPORT)


def main() -> int:
    logger.info("=== ①回填种子跑(真实历史价,无浏览器无真金)===")
    setup()
    cands = load_candidates()
    px = PricesDbClose(PRICES_DB)
    with LedgerWriter(LEDGER_DB) as w:
        broker = PaperBroker(w, px, BROKER_CFG)
        meta = replay(w, broker, cands)
        finalize_and_report(w, broker, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
