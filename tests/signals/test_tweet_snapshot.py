"""tweet_snapshot 测试:合成 sqlite 源 + 幂等/blocked/孤儿;真实库 skipif 只读快照。"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from src.signals.paths import STOCK_PICKER_HOME, TRACKRECORD_DB, TWEETS_DB, connect_readonly
from src.signals.tweet_snapshot import fetch_snapshot, snapshot_tweets

_TWEETS_DDL = """
CREATE TABLE tweets (
    id TEXT PRIMARY KEY, handle TEXT NOT NULL, author_id TEXT, username TEXT, display_name TEXT,
    created_at TEXT, fetched_at INTEGER NOT NULL, text TEXT, source TEXT,
    like_count INTEGER DEFAULT 0, retweet_count INTEGER DEFAULT 0, reply_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0, url TEXT, tickers TEXT, media TEXT, has_media INTEGER DEFAULT 0,
    sentiment TEXT, sentiment_score REAL, blocked INTEGER DEFAULT 0
)
"""

_ROWS = [
    # (id, handle, author_id, username, display_name, created_at, fetched_at, text, source,
    #  like, rt, reply, view, url, tickers, media, has_media, sentiment, sentiment_score, blocked)
    (
        "t1",
        "alpha",
        "100",
        "alpha",
        "Alpha",
        "2026-06-09T10:00:00+00:00",
        1780000000,
        "NVDA to the moon",
        "twscrape",
        5,
        1,
        0,
        100,
        "https://x.com/alpha/status/t1",
        '["NVDA"]',
        None,
        0,
        "bullish",
        0.9,
        0,
    ),
    (
        "t2",
        "beta",
        "200",
        "beta",
        "Beta",
        "2026-06-09T11:00:00+00:00",
        1780000100,
        "TSLA short setup",
        "twscrape",
        2,
        0,
        0,
        50,
        "https://x.com/beta/status/t2",
        '["TSLA"]',
        '[{"type":"photo","url":"https://pbs.example/img.jpg"}]',
        1,
        "bearish",
        -0.7,
        0,
    ),
    (
        "t3",
        "gamma",
        "300",
        "gamma",
        "Gamma",
        "2026-06-09T12:00:00+00:00",
        1780000200,
        "blocked content",
        "twscrape",
        0,
        0,
        0,
        10,
        "https://x.com/gamma/status/t3",
        "[]",
        None,
        0,
        "neutral",
        0.0,
        1,
    ),
]


@pytest.fixture
def src_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """合成 tweets.db 同 schema 的 tmp sqlite 源(3 行:含 1 blocked、1 media JSON)。"""
    conn = sqlite3.connect(tmp_path / "tweets_src.db")
    conn.execute(_TWEETS_DDL)
    conn.executemany(f"INSERT INTO tweets VALUES ({', '.join('?' * 20)})", _ROWS)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def snap_db(tmp_path: Path) -> Path:
    return tmp_path / "signal_snapshots.duckdb"


def test_snapshot_and_idempotent(src_conn: sqlite3.Connection, snap_db: Path) -> None:
    ids = ["t1", "t2", "t3"]
    assert snapshot_tweets(ids, db_path=snap_db, tweets_conn=src_conn) == 3
    # 幂等:再跑一次 0 新增,首次快照不被覆盖
    assert snapshot_tweets(ids, db_path=snap_db, tweets_conn=src_conn) == 0


def test_fetch_snapshot_fields(src_conn: sqlite3.Connection, snap_db: Path) -> None:
    snapshot_tweets(["t1", "t2", "t3"], db_path=snap_db, tweets_conn=src_conn)
    snap = fetch_snapshot("t1", db_path=snap_db)
    assert snap is not None
    assert snap["text"] == "NVDA to the moon"
    assert snap["url"] == "https://x.com/alpha/status/t1"
    assert snap["snapshot_ts"] is not None
    # media JSON 原样保留
    media_snap = fetch_snapshot("t2", db_path=snap_db)
    assert media_snap is not None and media_snap["has_media"] == 1 and "photo" in media_snap["media"]
    # blocked 标志保留(照存,内部可用)
    blocked_snap = fetch_snapshot("t3", db_path=snap_db)
    assert blocked_snap is not None and blocked_snap["blocked"] == 1


def test_orphan_ids_tolerated(src_conn: sqlite3.Connection, snap_db: Path) -> None:
    # 孤儿 id 不崩,只插入存在的;重复 id 去重
    assert snapshot_tweets(["t1", "ghost1", "t1", "ghost2"], db_path=snap_db, tweets_conn=src_conn) == 1
    assert fetch_snapshot("ghost1", db_path=snap_db) is None


def test_empty_and_missing_db(src_conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert snapshot_tweets([], db_path=tmp_path / "x.duckdb", tweets_conn=src_conn) == 0
    assert fetch_snapshot("t1", db_path=tmp_path / "nonexistent.duckdb") is None


def test_snapshot_ts_is_utc_instant(src_conn: sqlite3.Connection, snap_db: Path) -> None:
    """snapshot_ts 必须是带时区的真实时刻(旧 naive TIMESTAMP 按 PT 墙钟存,偏 7h)。"""
    before = datetime.now(UTC) - timedelta(seconds=2)
    snapshot_tweets(["t1"], db_path=snap_db, tweets_conn=src_conn)
    after = datetime.now(UTC) + timedelta(seconds=2)
    snap = fetch_snapshot("t1", db_path=snap_db)
    assert snap is not None
    ts = snap["snapshot_ts"]
    assert ts.tzinfo is not None  # tz-aware,跨机器语义唯一
    assert before <= ts.astimezone(UTC) <= after  # 偏 7h 的旧 bug 在此必炸


def test_legacy_naive_schema_rejected(src_conn: sqlite3.Connection, snap_db: Path) -> None:
    """旧 naive-TIMESTAMP 库被 CREATE IF NOT EXISTS 静默沿用——必须拒写,不混两种时间语义。"""
    con = duckdb.connect(str(snap_db))
    con.execute("CREATE TABLE tweet_snapshots (tweet_id TEXT PRIMARY KEY, snapshot_ts TIMESTAMP DEFAULT now())")
    con.close()
    with pytest.raises(RuntimeError, match="TIMESTAMP WITH TIME ZONE"):
        snapshot_tweets(["t1"], db_path=snap_db, tweets_conn=src_conn)


def test_snapshot_refuses_stock_picker_paths(src_conn: sqlite3.Connection) -> None:
    """防参数转置:db_path 落在 stock-picker 侧直接拒绝,绝不开写连接。"""
    for bad in (TWEETS_DB, STOCK_PICKER_HOME / "x.duckdb"):
        with pytest.raises(ValueError, match="只读侧"):
            snapshot_tweets(["t1"], db_path=bad, tweets_conn=src_conn)


@pytest.mark.skipif(
    not (TRACKRECORD_DB.exists() and TWEETS_DB.exists()),
    reason="本地无 stock-picker 真实库",
)
def test_real_recent_calls_snapshot(tmp_path: Path) -> None:
    """真实库只读:近 7 天 5 个 call 的 tweet_id 快照到 tmp duckdb。"""
    tr = connect_readonly(TRACKRECORD_DB)
    try:
        ids = [
            r[0]
            for r in tr.execute(
                "SELECT tweet_id FROM analyst_calls WHERE is_call = 1 AND call_date >= date('now', '-7 days') "
                "GROUP BY tweet_id ORDER BY MAX(call_ts) DESC LIMIT 5"
            ).fetchall()
        ]
    finally:
        tr.close()
    if not ids:
        pytest.skip("近 7 天无真实 call")

    snap_db = tmp_path / "real_snap.duckdb"
    assert snapshot_tweets(ids, db_path=snap_db) == len(ids)
    for tid in ids:
        snap = fetch_snapshot(tid, db_path=snap_db)
        assert snap is not None
        assert snap["text"], f"快照 text 为空: {tid}"
        assert str(snap["url"]).startswith("https"), f"url 非 https: {snap['url']}"
