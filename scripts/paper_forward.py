"""②每交易日前向 runner(P2 通过标准:连续 ≥2 周本地模拟盘空跑真实信号)。

一个交易日 d 的完整一轮(可由 cron 盘后 17:30 ET 调度):
  1. warm_prices:确保 d 的 followed 标的日收盘已落我方缓存(缺则 yfinance 拉)——
     ⚠️ 闸门=warm 的 missing 列表,**不是 price_meta.max_date**(SPCX 类:有行无价);
  2. 入场:规则引擎对入场日==d 的候选决策 → followed 经 PaperBroker 按 d 收盘建仓
     (走 Data close_on,yfinance 兜底;无价 fail-closed → skip no_price,顺延 ≤1 日记 drift);
  3. 退出:对所有未平仓单按 d 收盘评估(翻空>止损>21d);无价不强平、沿用上一 mark 标 stale;
  4. 持仓/账户快照 + parquet 导出(Dash 脱 MOCK);
  5. agent_runs 心跳(本轮起止/导出/异常)。

持久化 ledger(data/execution/ledger.duckdb),逐日累积、append-only;对同一天重跑幂等
(signal/enter 均按 signal_id 去重)。退出码:0=全覆盖正常 / 2=有标的缺价已逐票 skip /
3=warm 全 missing 疑 yfinance 挂(停+告警,不空跑成假数据)。

用法:uv run python scripts/paper_forward.py [--date YYYY-MM-DD](默认今日 ET)
"""

from __future__ import annotations

import argparse
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

from config.settings import get_settings  # noqa: E402
from src.execution.ledger import LedgerWriter, export_ledger  # noqa: E402
from src.execution.paper_broker import DataPriceSource, PaperBroker, PaperBrokerConfig  # noqa: E402
from src.rules.engine import PdtState, PortfolioState, Position, RuleParams, decide, entry_window  # noqa: E402
from src.rules.runner import fetch_recent_bearish  # noqa: E402
from src.signals.honest_leaderboard import proven  # noqa: E402

_ET = ZoneInfo("America/New_York")
SOURCE_SIGNALS_DB = Path.home() / "quant-stock" / "data" / "signals" / "signal_snapshots.duckdb"
INITIAL_CASH = Decimal("100000.00")
PARAMS = RuleParams()
BROKER_CFG = PaperBrokerConfig(per_order_usd=PARAMS.per_order_usd, slippage_bps=0.0, hold_days=21)


def _decision_ts(d: date) -> datetime:
    return datetime.combine(d, time(15, 30), tzinfo=_ET)


def _load_candidates(local_copy: Path) -> pl.DataFrame:
    """主树信号库只读拷贝后读(红线 1:不碰原件、不持锁)。"""
    local_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_SIGNALS_DB, local_copy)
    con = duckdb.connect(str(local_copy), read_only=True)
    try:
        return con.execute("SELECT * FROM signal_candidates").pl()
    finally:
        con.close()


def run_day(d: date) -> int:
    settings = get_settings()
    if not SOURCE_SIGNALS_DB.exists():
        logger.error("信号源不存在: {}", SOURCE_SIGNALS_DB)
        return 3

    cands = _load_candidates(settings.data_dir / "execution" / "_signals_copy.duckdb")
    price = DataPriceSource()
    tickers = sorted(cands["ticker"].unique().to_list())

    # ── 1. warm:闸门=missing 列表,不是 max_date ──
    rep = price.warm(tickers, d)
    logger.info("warm {}: {}", d, rep)
    if rep["covered"] + rep["fetched"] == 0:
        logger.error("warm 全 missing(疑 yfinance 挂),停止本轮不空跑假数据: {}", rep)
        return 3

    board = proven("21d")
    handle_wilson = {h: wl for h, wl in zip(board["handle"], board["wilson_lo"], strict=True) if wl is not None}

    settings.execution_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with LedgerWriter(settings.execution_ledger_path) as w:
        broker = PaperBroker(w, price, BROKER_CFG)
        run_id = w.start_agent_run(kill_switch=False, note=f"paper_forward {d}")
        signals_seen = orders_placed = 0
        error = None
        try:
            # ── 2. 入场:入场日==d 的候选 ──
            with_entry = cands.with_columns(
                pl.col("call_ts").map_elements(lambda t: entry_window(t)[0], return_dtype=pl.Date).alias("ed")
            )
            cohort = with_entry.filter(pl.col("ed") == d).drop("ed")
            bearish = fetch_recent_bearish(_decision_ts(d), set(handle_wilson))
            if cohort.height:
                prices = {}
                for t in cohort["ticker"].unique().to_list():
                    px = price.close_on(t, d)
                    if px is not None:
                        prices[t] = float(px)
                lots = broker.open_lots()
                portfolio = PortfolioState(
                    open_positions=tuple(Position(ticker=lt.ticker, handle=lt.handle) for lt in lots)
                )
                decisions = decide(
                    cohort,
                    decision_ts=_decision_ts(d),
                    pdt=PdtState(day_trades_5d=0, settled_cash=float(INITIAL_CASH)),
                    prices=prices,
                    portfolio=portfolio,
                    handle_wilson=handle_wilson,
                    recent_bearish=bearish,
                    params=PARAMS,
                )
                decided = cohort.join(decisions, on="signal_id", how="inner")
                for r in decided.to_dicts():
                    if w.insert_signal(
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
                    ):
                        signals_seen += 1
                for r in decided.filter(pl.col("decision") == "followed").to_dicts():
                    if broker.enter(
                        signal_id=r["signal_id"],
                        handle=r["handle"],
                        ticker=r["ticker"],
                        call_ts=r["call_ts"],
                        rule_version=PARAMS.rule_version,
                        entry_date=d,
                    ):
                        orders_placed += 1

            # ── 3. 退出评估 ──
            flips = {(b["handle"], b["ticker"]): _decision_ts(d).date() for b in bearish.to_dicts()}
            exited = broker.forward_day(d, rule_version=PARAMS.rule_version, flips=flips)

            # ── 4. 快照 + 水位 + 导出 ──
            n_pos = broker.snapshot_positions(d)
            w.insert_watermark(
                last_seen_call_ts=cands["call_ts"].max(), calls_seen=cands.height, note=f"paper_forward {d}"
            )
            export_ok = export_ledger(w.conn, settings.execution_export_dir)
            logger.info(
                "forward {}: followed+{} 平仓{} 持仓{} 导出={}", d, orders_placed, len(exited), n_pos, export_ok
            )
        except Exception as exc:  # noqa: BLE001 — 异常入心跳留证,不静默吞
            error = str(exc)
            logger.exception("forward {} 异常", d)
            export_ok = export_ledger(w.conn, settings.execution_export_dir)
        finally:
            w.finish_agent_run(
                run_id=run_id,
                signals_seen=signals_seen,
                orders_placed=orders_placed,
                export_ok=export_ok,
                error=error,
            )

    if error:
        return 3
    return 2 if rep["missing"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="每交易日本地模拟盘前向 runner")
    parser.add_argument("--date", default="", help="目标交易日 YYYY-MM-DD(默认今日 ET)")
    args = parser.parse_args(argv)
    d = date.fromisoformat(args.date) if args.date else datetime.now(UTC).astimezone(_ET).date()
    logger.info("=== paper_forward {} (本地模拟,无浏览器无真金)===", d)
    return run_day(d)


if __name__ == "__main__":
    raise SystemExit(main())
