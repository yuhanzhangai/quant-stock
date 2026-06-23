"""pipeline 测试:合成 trackrecord/tweets sqlite + CRLF 诚实榜 CSV + tmp DuckDB,全链路无真库。"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from src.signals.calls_poller import PollerState, poll_new_calls
from src.signals.pipeline import _OUT_COLS, build_candidates, persist_candidates, run_pipeline
from tests.signals.test_calls_poller import SCHEMA_SQL

_TWEETS_SCHEMA = """
CREATE TABLE tweets (
    id TEXT PRIMARY KEY, handle TEXT, author_id TEXT, username TEXT,
    created_at TEXT, fetched_at INTEGER, text TEXT, url TEXT,
    media TEXT, has_media INTEGER, blocked INTEGER DEFAULT 0,
    like_count INTEGER, retweet_count INTEGER, view_count INTEGER, tickers TEXT, sentiment TEXT
)
"""

# alice=PROVEN@21d、carol=PROVEN_BAD_1REGIME、dave=TRACKING、frank=PROVEN 但只有 5d
_CSV_HEADER = (
    "handle,horizon,n,hit_rate,wilson_lo,wilson_hi,bull_n,bull_hit,bear_n,bear_hit,"
    "avg_dir_abret,span_days,earliest,latest,cross_regime,status"
)
_CSV_ROWS = [
    "alice,21d,30,0.7,0.52,0.83,20,0.75,10,0.6,0.045,120,2025-12-01,2026-03-31,True,PROVEN",
    "carol,21d,22,0.3,0.15,0.5,22,0.3,0,,-0.02,80,2026-01-05,2026-03-26,False,PROVEN_BAD_1REGIME",
    "dave,21d,8,0.62,0.31,0.86,5,0.6,3,0.67,0.012,40,2026-02-01,2026-03-13,False,TRACKING",
    "frank,5d,40,0.72,0.56,0.84,30,0.7,10,0.8,0.02,150,2025-11-01,2026-03-31,True,PROVEN",
]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


@pytest.fixture
def calls_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(tmp_path / "trackrecord.db")
    c.execute(SCHEMA_SQL)
    yield c
    c.close()


@pytest.fixture
def tweets_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(tmp_path / "tweets.db")
    c.execute(_TWEETS_SCHEMA)
    yield c
    c.close()


@pytest.fixture
def leaderboard_csv(tmp_path: Path) -> Path:
    path = tmp_path / "leaderboard_honest_2026-06-01.csv"
    path.write_bytes(("\r\n".join([_CSV_HEADER, *_CSV_ROWS]) + "\r\n").encode())
    return path


@pytest.fixture
def snapshot_db(tmp_path: Path) -> Path:
    return tmp_path / "out" / "signal_snapshots.duckdb"


def insert_call(
    c: sqlite3.Connection, tweet_id: str, handle: str, ticker: str, ts: datetime, direction: str = "bullish"
) -> None:
    c.execute(
        "INSERT INTO analyst_calls (tweet_id, author_id, handle, ticker, direction, call_ts, call_date,"
        " is_call, confidence, conviction) VALUES (?, 'a1', ?, ?, ?, ?, ?, 1, 0.9, 'high')",
        (tweet_id, handle, ticker, direction, _iso(ts), ts.date().isoformat()),
    )
    c.commit()


def insert_tweet(c: sqlite3.Connection, tweet_id: str, handle: str, ts: datetime, blocked: int = 0) -> None:
    c.execute(
        "INSERT INTO tweets (id, handle, created_at, fetched_at, text, url, blocked) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            tweet_id,
            handle,
            _iso(ts),
            int(ts.timestamp()) + 60,
            f"text of {tweet_id}",
            f"https://x.com/{handle}/status/{tweet_id}",
            blocked,
        ),
    )
    c.commit()


def _poll(conn: sqlite3.Connection) -> pl.DataFrame:
    df, _ = poll_new_calls(PollerState(), conn=conn)
    return df


def _build(
    calls: pl.DataFrame, leaderboard_csv: Path, snapshot_db: Path, tweets_conn: sqlite3.Connection
) -> pl.DataFrame:
    return build_candidates(calls, leaderboard_path=leaderboard_csv, snapshot_db=snapshot_db, tweets_conn=tweets_conn)


def test_happy_path_signals_schema(calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    insert_call(calls_conn, "t1", "alice", "NVDA", now - timedelta(hours=1))
    insert_tweet(tweets_conn, "t1", "alice", now - timedelta(hours=1))
    cand = _build(_poll(calls_conn), leaderboard_csv, snapshot_db, tweets_conn)
    assert cand.height == 1
    assert cand.columns == list(_OUT_COLS)
    row = cand.row(0, named=True)
    assert row["signal_id"] == "sig_t1_NVDA"  # r2 口径:sig_||tweet_id||_||ticker
    assert row["tier"] == "PROVEN"
    assert row["tier_csv_date"] == date(2026, 6, 1)
    assert row["tweet_text"] == "text of t1"
    assert row["tweet_url"].endswith("/t1")
    assert row["tweet_blocked"] is False
    assert row["conviction"] == "high" and row["confidence"] == 0.9
    assert row["call_ts"].tzinfo is not None and row["ingested_ts"].tzinfo is not None


def test_filters_tier_horizon_direction(calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    insert_call(calls_conn, "t1", "carol", "NVDA", now)  # PROVEN_BAD_1REGIME:精确匹配挡掉
    insert_call(calls_conn, "t2", "dave", "NVDA", now)  # TRACKING
    insert_call(calls_conn, "t3", "frank", "NVDA", now)  # PROVEN 但只有 5d,21d 口径无此人
    insert_call(calls_conn, "t4", "nobody", "NVDA", now)  # 不在榜
    insert_call(calls_conn, "t5", "alice", "NVDA", now, "bearish")  # PROVEN 但反向
    for t in ("t1", "t2", "t3", "t4", "t5"):
        insert_tweet(tweets_conn, t, "x", now)
    cand = _build(_poll(calls_conn), leaderboard_csv, snapshot_db, tweets_conn)
    assert cand.is_empty()


def test_conflict_filter_same_handle_ticker(calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    # NVDA:bullish 后 1h 同人反向 → 剔除;TSLA:反向在前 → 保留;AMD:别人翻向不影响 alice
    insert_call(calls_conn, "t1", "alice", "NVDA", now - timedelta(hours=2))
    insert_call(calls_conn, "t2", "alice", "NVDA", now - timedelta(hours=1), "bearish")
    insert_call(calls_conn, "t3", "alice", "TSLA", now - timedelta(hours=1))
    insert_call(calls_conn, "t4", "alice", "TSLA", now - timedelta(hours=2), "bearish")
    insert_call(calls_conn, "t5", "alice", "AMD", now - timedelta(hours=2))
    insert_call(calls_conn, "t6", "dave", "AMD", now - timedelta(hours=1), "bearish")
    for t in ("t1", "t3", "t5"):
        insert_tweet(tweets_conn, t, "alice", now - timedelta(hours=2))
    cand = _build(_poll(calls_conn), leaderboard_csv, snapshot_db, tweets_conn)
    assert sorted(cand.get_column("signal_id").to_list()) == ["sig_t3_TSLA", "sig_t5_AMD"]


def test_multi_ticker_same_tweet_distinct_ids(calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    insert_call(calls_conn, "t1", "alice", "NVDA", now)
    insert_call(calls_conn, "t1", "alice", "TSLA", now)  # 同帖两票:上游 PK (tweet_id, ticker)
    insert_tweet(tweets_conn, "t1", "alice", now)
    cand = _build(_poll(calls_conn), leaderboard_csv, snapshot_db, tweets_conn)
    assert sorted(cand.get_column("signal_id").to_list()) == ["sig_t1_NVDA", "sig_t1_TSLA"]


def test_orphan_without_snapshot_dropped(calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    insert_call(calls_conn, "ghost", "alice", "NVDA", now)  # tweets 库无原帖
    cand = _build(_poll(calls_conn), leaderboard_csv, snapshot_db, tweets_conn)
    assert cand.is_empty()  # tweet_text NOT NULL:无快照原件不出候选


def test_blocked_flag_carried(calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    insert_call(calls_conn, "t1", "alice", "NVDA", now)
    insert_tweet(tweets_conn, "t1", "alice", now, blocked=1)
    cand = _build(_poll(calls_conn), leaderboard_csv, snapshot_db, tweets_conn)
    assert cand.get_column("tweet_blocked").to_list() == [True]


def test_run_pipeline_idempotent(tmp_path, calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    insert_call(calls_conn, "t1", "alice", "NVDA", now - timedelta(hours=1))
    insert_tweet(tweets_conn, "t1", "alice", now - timedelta(hours=1))
    kwargs = dict(
        state_path=tmp_path / "state.json",
        snapshot_db=snapshot_db,
        leaderboard_path=leaderboard_csv,
        calls_conn=calls_conn,
        tweets_conn=tweets_conn,
    )
    r1 = run_pipeline(**kwargs)
    assert r1 == {"calls_seen": 1, "candidates": 1, "inserted": 1}
    r2 = run_pipeline(**kwargs)  # 水位已推进:无新喊单、零重插
    assert r2 == {"calls_seen": 0, "candidates": 0, "inserted": 0}
    con = duckdb.connect(str(snapshot_db), read_only=True)
    try:
        row = con.execute("SELECT count(*) FROM signal_candidates").fetchone()
        assert row is not None and row[0] == 1
    finally:
        con.close()


def test_persist_rejects_dup_and_is_idempotent(calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    insert_call(calls_conn, "t1", "alice", "NVDA", now)
    insert_tweet(tweets_conn, "t1", "alice", now)
    cand = _build(_poll(calls_conn), leaderboard_csv, snapshot_db, tweets_conn)
    assert persist_candidates(cand, snapshot_db) == 1
    assert persist_candidates(cand, snapshot_db) == 0  # 同批重放:signal_id 已存在,不覆盖不报错


def test_candidates_timestamps_are_timestamptz(calls_conn, tweets_conn, leaderboard_csv, snapshot_db) -> None:
    now = datetime.now(UTC)
    insert_call(calls_conn, "t1", "alice", "NVDA", now)
    insert_tweet(tweets_conn, "t1", "alice", now)
    persist_candidates(_build(_poll(calls_conn), leaderboard_csv, snapshot_db, tweets_conn), snapshot_db)
    con = duckdb.connect(str(snapshot_db), read_only=True)
    try:
        types = dict(
            con.execute(
                "SELECT column_name, data_type FROM duckdb_columns()"
                " WHERE table_name = 'signal_candidates' AND column_name IN ('call_ts', 'ingested_ts')"
            ).fetchall()
        )
    finally:
        con.close()
    # naive TIMESTAMP 按 session 时区取墙钟,审计时间戳必须 TIMESTAMPTZ(预研 high 发现,守卫不回退)
    assert types == {"call_ts": "TIMESTAMP WITH TIME ZONE", "ingested_ts": "TIMESTAMP WITH TIME ZONE"}
