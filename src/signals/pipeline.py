"""最小信号管线:水位轮询 → PROVEN@21d × bullish 过滤 → 批内冲突过滤 → signal 候选落库。

输出列对齐 docs/ORDER_LEDGER_SPEC.md §4.1 signals 表口径:
`signal_id = 'sig_' || tweet_id || '_' || ticker`(r2 撞键修正:上游复合主键 (tweet_id, ticker),
同帖喊多只票是真实场景,必须含 ticker)。`decision` / `decision_reason` / `rule_version` 三列归
下游跟单规则引擎填写,本层不产、不假装产。候选幂等落 `signal_candidates` 表(signal_id 主键,
重复轮询不重插),供 Valid S 账(FOLLOW_PERF_SPEC §1,counterfactual 口径)消费。

冲突过滤(批内):同 handle×ticker 若存在 call_ts 不早于该 bullish 喊单的反向(非 bullish)
喊单,则该 bullish 不出候选——收录时点该博主已翻向,入场依据失效。跨批的翻向属于引擎层
direction_flip 退出逻辑(ORDER_LEDGER_SPEC §6),不在本层。
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import polars as pl
from loguru import logger

from src.signals.calls_poller import DEFAULT_STATE_PATH, load_state, poll_new_calls, save_state
from src.signals.honest_leaderboard import csv_date, discover_latest_csv, proven
from src.signals.paths import assert_writable_path
from src.signals.tweet_snapshot import DEFAULT_SNAPSHOT_DB, snapshot_tweets

_CHUNK = 500

# 列序 = signals 表(ORDER_LEDGER_SPEC §4.1)中本层可产的子集,引擎列(decision/…)除外
_OUT_SCHEMA = pl.Schema(
    {
        "signal_id": pl.String,
        "tweet_id": pl.String,
        "handle": pl.String,
        "author_id": pl.String,
        "tier": pl.String,
        "tier_csv_date": pl.Date,
        "ticker": pl.String,
        "direction": pl.String,
        "call_ts": pl.Datetime("us", "UTC"),
        "ingested_ts": pl.Datetime("us", "UTC"),
        "tweet_text": pl.String,
        "tweet_url": pl.String,
        "tweet_created_at": pl.Datetime("us", "UTC"),
        "tweet_blocked": pl.Boolean,
        "conviction": pl.String,
        "confidence": pl.Float64,
    }
)
_OUT_COLS = tuple(_OUT_SCHEMA.names())

# 时间戳一律 TIMESTAMPTZ:naive TIMESTAMP 会按 session 时区取墙钟,审计取证不可恢复(同 tweet_snapshot)
_DDL = """
CREATE TABLE IF NOT EXISTS signal_candidates (
    signal_id        TEXT PRIMARY KEY,
    tweet_id         TEXT NOT NULL,
    handle           TEXT NOT NULL,
    author_id        TEXT,
    tier             TEXT NOT NULL,
    tier_csv_date    DATE NOT NULL,
    ticker           TEXT NOT NULL,
    direction        TEXT NOT NULL,
    call_ts          TIMESTAMPTZ NOT NULL,
    ingested_ts      TIMESTAMPTZ NOT NULL,
    tweet_text       TEXT NOT NULL,
    tweet_url        TEXT NOT NULL,
    tweet_created_at TIMESTAMPTZ,
    tweet_blocked    BOOLEAN NOT NULL,
    conviction       TEXT,
    confidence       DOUBLE
)
"""


def _assert_candidates_tz(con: duckdb.DuckDBPyConnection) -> None:
    """守卫:call_ts / ingested_ts 必须 TIMESTAMPTZ,旧 naive schema 拒绝混写。"""
    rows = dict(
        con.execute(
            "SELECT column_name, data_type FROM duckdb_columns() "
            "WHERE table_name = 'signal_candidates' AND column_name IN ('call_ts', 'ingested_ts')"
        ).fetchall()
    )
    for col in ("call_ts", "ingested_ts"):
        if rows.get(col) != "TIMESTAMP WITH TIME ZONE":
            raise RuntimeError(
                f"signal_candidates.{col} 类型为 {rows.get(col, '缺失')},需 TIMESTAMP WITH TIME ZONE;"
                "旧库请先迁移,拒绝混写两种时间语义"
            )


def _read_snapshots(tweet_ids: list[str], db_path: Path) -> pl.DataFrame:
    """从我方快照库读原帖字段(点时副本,不回读 stock-picker)。"""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows: list[tuple] = []
        for i in range(0, len(tweet_ids), _CHUNK):
            chunk = tweet_ids[i : i + _CHUNK]
            ph = ", ".join("?" * len(chunk))
            rows.extend(
                con.execute(
                    f"SELECT tweet_id, text, url, created_at, blocked FROM tweet_snapshots WHERE tweet_id IN ({ph})",
                    chunk,
                ).fetchall()
            )
    finally:
        con.close()
    return pl.DataFrame(
        rows,
        schema={
            "tweet_id": pl.String,
            "tweet_text": pl.String,
            "tweet_url": pl.String,
            "created_at_raw": pl.String,
            "blocked_raw": pl.Int64,
        },
        orient="row",
    )


def build_candidates(
    calls: pl.DataFrame,
    *,
    horizon: str = "21d",
    leaderboard_path: Path | None = None,
    snapshot_db: Path = DEFAULT_SNAPSHOT_DB,
    tweets_conn: sqlite3.Connection | None = None,
) -> pl.DataFrame:
    """polled 新喊单 → PROVEN@horizon × bullish × 批内无翻向 → 原帖快照 → signals 口径候选行。"""
    if calls.is_empty():
        return pl.DataFrame(schema=_OUT_SCHEMA)
    board_path = leaderboard_path if leaderboard_path is not None else discover_latest_csv()
    tier_date = csv_date(board_path)
    if tier_date is None:
        raise ValueError(f"诚实榜文件名无法提取日期(tier_csv_date 溯源必填): {board_path}")
    board = proven(horizon, board_path).select("handle", pl.col("status").alias("tier")).unique(subset="handle")

    bullish = calls.filter(pl.col("direction") == "bullish").join(board, on="handle", how="inner")
    # 批内冲突过滤:同 handle×ticker 的反向喊单 call_ts >= 本喊单 → 已翻向,剔除(字典序=时序,poller 已断言格式)
    flips = (
        calls.filter(pl.col("direction") != "bullish")
        .group_by("handle", "ticker")
        .agg(pl.col("call_ts").max().alias("last_flip_ts"))
    )
    bullish = bullish.join(flips, on=["handle", "ticker"], how="left")
    conflicted = bullish.filter(pl.col("last_flip_ts") >= pl.col("call_ts"))
    if conflicted.height:
        logger.info(
            "冲突过滤剔除 {} 条(批内同 handle×ticker 已翻向): {}",
            conflicted.height,
            conflicted.select("handle", "ticker", "tweet_id").rows(),
        )
    cand = bullish.filter(pl.col("last_flip_ts").is_null() | (pl.col("last_flip_ts") < pl.col("call_ts"))).drop(
        "last_flip_ts"
    )
    if cand.is_empty():
        return pl.DataFrame(schema=_OUT_SCHEMA)

    ids = cand.get_column("tweet_id").unique().to_list()
    snapshot_tweets(ids, db_path=snapshot_db, tweets_conn=tweets_conn)
    out = cand.join(_read_snapshots(ids, snapshot_db), on="tweet_id", how="inner").filter(
        pl.col("tweet_text").is_not_null() & pl.col("tweet_url").is_not_null()
    )
    if out.height < cand.height:
        # tweet_text/tweet_url 是 signals 表 NOT NULL:无快照原件就不出候选(留档纪律,不造空依据)
        logger.warning("剔除 {} 条无原帖快照/缺 text/url 的候选(孤儿,日级对账兜底)", cand.height - out.height)
    return (
        out.with_columns(
            pl.format("sig_{}_{}", pl.col("tweet_id"), pl.col("ticker")).alias("signal_id"),
            pl.lit(tier_date).alias("tier_csv_date"),
            pl.col("call_ts").str.to_datetime(time_zone="UTC"),
            pl.lit(datetime.now(UTC)).alias("ingested_ts"),
            pl.col("created_at_raw").str.to_datetime(time_zone="UTC", strict=False).alias("tweet_created_at"),
            (pl.col("blocked_raw") == 1).fill_null(value=False).alias("tweet_blocked"),
        )
        .select(_OUT_COLS)
        .cast(_OUT_SCHEMA)
    )


def persist_candidates(candidates: pl.DataFrame, db_path: Path = DEFAULT_SNAPSHOT_DB) -> int:
    """候选幂等落 signal_candidates(signal_id 主键,已存在跳过不覆盖),返回新插入行数。"""
    if candidates.is_empty():
        return 0
    batch = candidates.unique(subset=["signal_id"], keep="first", maintain_order=True)
    assert_writable_path(db_path)  # 防参数转置:绝不对 stock-picker 侧文件开写连接
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(_DDL)
        _assert_candidates_tz(con)
        con.register("cand_batch", batch)
        cols = ", ".join(_OUT_COLS)
        count_sql = "SELECT count(*) FROM signal_candidates"
        before = con.execute(count_sql).fetchone()
        con.execute(
            f"INSERT INTO signal_candidates ({cols}) SELECT {cols} FROM cand_batch "
            f"WHERE signal_id NOT IN (SELECT signal_id FROM signal_candidates)"
        )
        after = con.execute(count_sql).fetchone()
        inserted = int(after[0]) - int(before[0])  # type: ignore[index]
        logger.info(
            "signal_candidates 落库: 批内 {} / 新增 {} / 已存在 {}", batch.height, inserted, batch.height - inserted
        )
        return inserted
    finally:
        con.close()


def run_pipeline(
    *,
    state_path: Path = DEFAULT_STATE_PATH,
    snapshot_db: Path = DEFAULT_SNAPSHOT_DB,
    overlap_hours: float = 6.0,
    bootstrap_days: float = 7.0,
    horizon: str = "21d",
    leaderboard_path: Path | None = None,
    calls_conn: sqlite3.Connection | None = None,
    tweets_conn: sqlite3.Connection | None = None,
) -> dict[str, int]:
    """一轮管线:轮询 → 过滤 → 快照 → 落候选。候选落库成功后才推水位(半途失败下轮重拉,落库幂等防重)。"""
    state = load_state(state_path)
    calls, new_state = poll_new_calls(state, overlap_hours, bootstrap_days, conn=calls_conn)
    cands = build_candidates(
        calls, horizon=horizon, leaderboard_path=leaderboard_path, snapshot_db=snapshot_db, tweets_conn=tweets_conn
    )
    inserted = persist_candidates(cands, snapshot_db)
    save_state(new_state, state_path)
    logger.info("pipeline 轮次完成: 新喊单 {} / 候选 {} / 新落库 {}", calls.height, cands.height, inserted)
    return {"calls_seen": calls.height, "candidates": cands.height, "inserted": inserted}


if __name__ == "__main__":
    run_pipeline()
