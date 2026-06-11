"""规则引擎 runner:signal_candidates 未决行 → decide() → rule_decisions 幂等落库。

P1 范围(spec §8 决策持久化):决策三列先留档在本仓 rule_decisions 表(insert-only,
signal_id 主键);Exec ledger(ORDER_LEDGER_SPEC signals 表)落地后由其 writer 消费合入,
本表保留为引擎侧审计留档。**prices 由调用方注入**——P2 接 Firstrade 读层之前没有权威的
决策时价格源,不假装有。stock-picker 侧(analyst_calls)一律只读直查(冲突门数据面)。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
from loguru import logger

from src.rules.engine import (
    OUT_SCHEMA,
    PdtState,
    PortfolioState,
    RuleParams,
    decide,
)
from src.signals.honest_leaderboard import proven
from src.signals.paths import TRACKRECORD_DB, assert_writable_path, connect_readonly
from src.signals.tweet_snapshot import DEFAULT_SNAPSHOT_DB

_BEARISH_SCHEMA = pl.Schema({"handle": pl.String, "ticker": pl.String, "call_ts": pl.String})

_DDL = """
CREATE TABLE IF NOT EXISTS rule_decisions (
    signal_id       TEXT PRIMARY KEY,
    decision        TEXT NOT NULL CHECK (decision IN ('followed', 'skipped')),
    decision_reason TEXT NOT NULL,
    rule_version    TEXT NOT NULL,
    decided_ts      TIMESTAMPTZ NOT NULL
)
"""


def load_undecided(db_path: Path = DEFAULT_SNAPSHOT_DB) -> pl.DataFrame:
    """signal_candidates 中尚无决策行的候选(rule_decisions 表未建时即全部候选)。"""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        if "signal_candidates" not in tables:
            raise RuntimeError(f"{db_path} 无 signal_candidates 表(先跑 Data 信号管线)")
        if "rule_decisions" not in tables:
            return con.execute("SELECT * FROM signal_candidates").pl()
        return con.execute(
            "SELECT c.* FROM signal_candidates c LEFT JOIN rule_decisions d USING (signal_id) "
            "WHERE d.signal_id IS NULL"
        ).pl()
    finally:
        con.close()


def fetch_recent_bearish(
    decision_ts: datetime,
    handles: set[str],
    lookback_days: float = 7.0,
    conn: sqlite3.Connection | None = None,
) -> pl.DataFrame:
    """冲突门证据:窗口内 PROVEN handle 的 bearish 喊单(analyst_calls 只读直查,spec §1.3)。"""
    lo = (decision_ts - timedelta(days=lookback_days)).astimezone(UTC).isoformat(timespec="seconds")
    hi = decision_ts.astimezone(UTC).isoformat(timespec="seconds")
    owned = conn is None
    if conn is None:
        conn = connect_readonly(TRACKRECORD_DB)
    try:
        rows = conn.execute(
            "SELECT handle, ticker, call_ts FROM analyst_calls "
            "WHERE is_call = 1 AND direction = 'bearish' AND call_ts > ? AND call_ts <= ?",
            (lo, hi),
        ).fetchall()
    finally:
        if owned:
            conn.close()
    df = pl.DataFrame(rows, schema=_BEARISH_SCHEMA, orient="row")
    return (df.filter(pl.col("handle").is_in(sorted(handles)))
            .with_columns(pl.col("call_ts").str.to_datetime(time_zone="UTC")))


def persist_decisions(decisions: pl.DataFrame, db_path: Path = DEFAULT_SNAPSHOT_DB) -> int:
    """决策行幂等落库(signal_id 已有决策的跳过不覆盖——决策是终态),返回新插入行数。"""
    if decisions.is_empty():
        return 0
    batch = (decisions.unique(subset=["signal_id"], keep="first", maintain_order=True)
             .with_columns(pl.lit(datetime.now(UTC)).alias("decided_ts")))
    assert_writable_path(db_path)  # 防参数转置:绝不对 stock-picker 侧文件开写连接
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(_DDL)
        con.register("dec_batch", batch)
        cols = ", ".join((*OUT_SCHEMA.names(), "decided_ts"))
        before = con.execute("SELECT count(*) FROM rule_decisions").fetchone()
        con.execute(f"INSERT INTO rule_decisions ({cols}) SELECT {cols} FROM dec_batch "
                    f"WHERE signal_id NOT IN (SELECT signal_id FROM rule_decisions)")
        after = con.execute("SELECT count(*) FROM rule_decisions").fetchone()
        inserted = int(after[0]) - int(before[0])  # type: ignore[index]
        logger.info("rule_decisions 落库: 批内 {} / 新增 {} / 已存在 {}",
                    batch.height, inserted, batch.height - inserted)
        return inserted
    finally:
        con.close()


def run_decision_cycle(
    *,
    prices: dict[str, float],
    pdt: PdtState,
    portfolio: PortfolioState | None = None,
    db_path: Path = DEFAULT_SNAPSHOT_DB,
    decision_ts: datetime | None = None,
    kill_switch: bool = False,
    blocked_handles: frozenset[str] = frozenset(),
    blocked_tickers: frozenset[str] = frozenset(),
    horizon: str = "21d",
    leaderboard_path: Path | None = None,
    calls_conn: sqlite3.Connection | None = None,
    params: RuleParams | None = None,
) -> dict[str, int]:
    """一轮决策循环(spec §0,15:30 ET 调度由上层负责):载入未决 → 决策 → 幂等落库。"""
    ts = decision_ts if decision_ts is not None else datetime.now(UTC)
    cands = load_undecided(db_path)
    if cands.is_empty():
        logger.info("decision cycle: 无未决候选")
        return {"undecided": 0, "decided": 0, "followed": 0, "inserted": 0}
    board = proven(horizon, leaderboard_path)
    handle_wilson = {h: w for h, w in zip(board["handle"], board["wilson_lo"], strict=True) if w is not None}
    bearish = fetch_recent_bearish(ts, set(handle_wilson), conn=calls_conn)
    decisions = decide(
        cands, decision_ts=ts, pdt=pdt, prices=prices, portfolio=portfolio,
        handle_wilson=handle_wilson, recent_bearish=bearish, kill_switch=kill_switch,
        blocked_handles=blocked_handles, blocked_tickers=blocked_tickers, params=params,
    )
    inserted = persist_decisions(decisions, db_path)
    followed = decisions.filter(pl.col("decision") == "followed").height
    logger.info("decision cycle 完成: 未决 {} / 出决策 {} / followed {} / 新落库 {}",
                cands.height, decisions.height, followed, inserted)
    return {"undecided": cands.height, "decided": decisions.height,
            "followed": followed, "inserted": inserted}
