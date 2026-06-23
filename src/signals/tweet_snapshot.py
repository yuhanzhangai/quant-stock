"""喊单原帖点时快照:从 tweets.db(只读)抄底 text/url/元数据到我方 DuckDB。

幂等设计:tweet_id 主键,首次快照即"下单依据"的点时副本,重复调用不覆盖。
blocked=1 的推文照存(内部审计可用),但 warning 提醒对外展示别用。
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
from loguru import logger

from src.signals.paths import SIGNALS_DATA_DIR, TWEETS_DB, assert_writable_path, connect_readonly

DEFAULT_SNAPSHOT_DB = SIGNALS_DATA_DIR / "signal_snapshots.duckdb"
_CHUNK_SIZE = 500
# tweets.db 源列(id 在快照表里改名 tweet_id 作主键,其余同名)
_SRC_COLS = (
    "id",
    "handle",
    "author_id",
    "username",
    "created_at",
    "fetched_at",
    "text",
    "url",
    "media",
    "has_media",
    "blocked",
    "like_count",
    "retweet_count",
    "view_count",
    "tickers",
    "sentiment",
)
_SNAP_COLS = ("tweet_id", *_SRC_COLS[1:])

_DDL = """
CREATE TABLE IF NOT EXISTS tweet_snapshots (
    tweet_id      TEXT PRIMARY KEY,
    handle        TEXT,
    author_id     TEXT,
    username      TEXT,
    created_at    TEXT,
    fetched_at    BIGINT,
    text          TEXT,
    url           TEXT,
    media         TEXT,
    has_media     INTEGER,
    blocked       INTEGER,
    like_count    INTEGER,
    retweet_count INTEGER,
    view_count    INTEGER,
    tickers       TEXT,
    sentiment     TEXT,
    -- 必须 TIMESTAMPTZ:naive TIMESTAMP 会按 session 时区取墙钟(PT 机器偏 -7h),
    -- 审计点时戳跨机器/容器不可恢复地混入两种语义
    snapshot_ts   TIMESTAMPTZ DEFAULT now()
)
"""


def _chunks(items: list[str], size: int = _CHUNK_SIZE) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _fetch_source_rows(conn: sqlite3.Connection, ids: list[str]) -> list[tuple[Any, ...]]:
    """按 id 分块(500/块)从 tweets.db 取源行。"""
    rows: list[tuple[Any, ...]] = []
    select_sql = f"SELECT {', '.join(_SRC_COLS)} FROM tweets WHERE id IN ({{ph}})"
    for chunk in _chunks(ids):
        ph = ", ".join("?" * len(chunk))
        rows.extend(conn.execute(select_sql.format(ph=ph), chunk).fetchall())
    return rows


def _assert_snapshot_ts_tz(con: duckdb.DuckDBPyConnection) -> None:
    """守卫:snapshot_ts 必须 TIMESTAMPTZ。旧 schema(naive TIMESTAMP)按 session 时区取墙钟,
    与新行混写后两种语义不可区分——拒绝继续写,提示手动迁移。"""
    row = con.execute(
        "SELECT data_type FROM duckdb_columns() WHERE table_name = 'tweet_snapshots' AND column_name = 'snapshot_ts'"
    ).fetchone()
    if row is None or row[0] != "TIMESTAMP WITH TIME ZONE":
        raise RuntimeError(
            f"tweet_snapshots.snapshot_ts 类型为 {row[0] if row else '缺失'},需 TIMESTAMP WITH TIME ZONE;"
            "旧库请先迁移(naive 值按写入机器时区解释后转 TIMESTAMPTZ),拒绝混写两种时间语义"
        )


def snapshot_tweets(
    tweet_ids: list[str],
    db_path: Path = DEFAULT_SNAPSHOT_DB,
    tweets_conn: sqlite3.Connection | None = None,
) -> int:
    """把喊单原帖快照到我方 DuckDB(幂等:已存在的 tweet_id 跳过,不覆盖),返回新插入行数。"""
    ids = list(dict.fromkeys(tweet_ids))  # 去重保序
    if not ids:
        return 0

    own_conn = tweets_conn is None
    conn = connect_readonly(TWEETS_DB) if tweets_conn is None else tweets_conn
    try:
        rows = _fetch_source_rows(conn, ids)
    finally:
        if own_conn:
            conn.close()

    found = {r[0] for r in rows}
    orphans = [i for i in ids if i not in found]
    if orphans:
        logger.warning("快照源库缺失 {} 个 tweet_id(孤儿,跳过): {}", len(orphans), orphans)
    blocked_n = sum(1 for r in rows if r[10] == 1)  # blocked 列位于 _SRC_COLS[10]
    if blocked_n:
        logger.warning("本批快照含 {} 条 blocked=1 推文:照存供内部审计,对外展示禁用", blocked_n)

    assert_writable_path(db_path)  # 防参数转置:绝不对 stock-picker 侧文件开写连接
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(_DDL)
        _assert_snapshot_ts_tz(con)  # 旧 naive-TIMESTAMP 库会被 IF NOT EXISTS 静默沿用,必须 fail loud
        existing: set[str] = set()
        for chunk in _chunks(sorted(found)):
            ph = ", ".join("?" * len(chunk))
            check_sql = f"SELECT tweet_id FROM tweet_snapshots WHERE tweet_id IN ({ph})"
            existing |= {r[0] for r in con.execute(check_sql, chunk).fetchall()}
        new_rows = [r for r in rows if r[0] not in existing]
        if new_rows:
            ph = ", ".join("?" * len(_SNAP_COLS))
            con.executemany(f"INSERT OR IGNORE INTO tweet_snapshots ({', '.join(_SNAP_COLS)}) VALUES ({ph})", new_rows)
        logger.info(
            "tweet 快照完成: 请求 {} / 新增 {} / 已存在 {} / 孤儿 {}",
            len(ids),
            len(new_rows),
            len(existing),
            len(orphans),
        )
        return len(new_rows)
    finally:
        con.close()


def fetch_snapshot(tweet_id: str, db_path: Path = DEFAULT_SNAPSHOT_DB) -> dict[str, Any] | None:
    """取单条快照(给"每单附依据"用);无库/无表/无此 id 返回 None。"""
    if not db_path.exists():
        return None
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        cur = con.execute("SELECT * FROM tweet_snapshots WHERE tweet_id = ?", [tweet_id])
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description or []]
        return dict(zip(cols, row, strict=True))
    except duckdb.CatalogException:
        return None
    finally:
        con.close()
