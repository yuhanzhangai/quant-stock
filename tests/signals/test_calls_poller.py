"""calls_poller 测试:合成 tmp sqlite 库验证水位/overlap/去重;真实库只读 smoke。"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.signals.calls_poller import (
    _CALL_COLUMNS,
    PollerState,
    load_state,
    measure_ingest_latency,
    poll_new_calls,
    save_state,
)
from src.signals.paths import STOCK_PICKER_HOME, TRACKRECORD_DB, TWEETS_DB

SCHEMA_SQL = """
CREATE TABLE analyst_calls (
    tweet_id    TEXT NOT NULL,
    author_id   TEXT,
    handle      TEXT,
    ticker      TEXT NOT NULL,
    direction   TEXT NOT NULL,
    call_ts     TEXT,
    call_date   TEXT,
    is_call     INTEGER DEFAULT 1,
    extraction  TEXT DEFAULT 'heuristic',
    confidence  REAL,
    conviction  TEXT,
    PRIMARY KEY (tweet_id, ticker)
)
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(tmp_path / "trackrecord.db")
    c.execute(SCHEMA_SQL)
    yield c
    c.close()


def insert_call(c: sqlite3.Connection, tweet_id: str, ticker: str, ts: datetime, is_call: int = 1) -> None:
    c.execute(
        "INSERT INTO analyst_calls (tweet_id, author_id, handle, ticker, direction, call_ts, call_date, is_call)"
        " VALUES (?, 'a1', 'h1', ?, 'bullish', ?, ?, ?)",
        (tweet_id, ticker, _iso(ts), ts.date().isoformat(), is_call),
    )
    c.commit()


def test_bootstrap_window_only(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    insert_call(conn, "t1", "NVDA", now - timedelta(days=1))
    insert_call(conn, "t2", "TSLA", now - timedelta(days=3))
    insert_call(conn, "t3", "AAPL", now - timedelta(days=10))  # 超出 bootstrap 窗口
    insert_call(conn, "t4", "META", now - timedelta(days=1), is_call=0)  # 非 call
    df, state = poll_new_calls(PollerState(), bootstrap_days=7.0, conn=conn)
    assert sorted(df.get_column("tweet_id").to_list()) == ["t1", "t2"]
    assert df.columns == list(_CALL_COLUMNS)
    assert state.last_seen_call_ts == _iso(now - timedelta(days=1))


def test_no_new_rows_watermark_unchanged(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    insert_call(conn, "t1", "NVDA", now - timedelta(hours=2))
    _, s1 = poll_new_calls(PollerState(), conn=conn)
    df2, s2 = poll_new_calls(s1, conn=conn)
    assert df2.is_empty()
    assert s2.last_seen_call_ts == s1.last_seen_call_ts
    assert s2.seen_recent == s1.seen_recent


def test_late_row_within_overlap_caught_once(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    insert_call(conn, "t1", "NVDA", now)
    _, s1 = poll_new_calls(PollerState(), overlap_hours=6.0, conn=conn)
    # 迟到行:call_ts 比水位老 2h(在 overlap 内),水位推进后才入库
    insert_call(conn, "late", "TSLA", now - timedelta(hours=2))
    df2, s2 = poll_new_calls(s1, overlap_hours=6.0, conn=conn)
    assert df2.get_column("tweet_id").to_list() == ["late"]
    assert s2.last_seen_call_ts == s1.last_seen_call_ts  # 水位单调,不被老行拉回
    df3, _ = poll_new_calls(s2, overlap_hours=6.0, conn=conn)
    assert df3.is_empty()  # 不重复返回已见行


def test_late_row_beyond_overlap_is_missed(conn: sqlite3.Connection) -> None:
    """已知取舍:迟到超过 overlap 的行会漏(取值依据见模块 docstring,兜底靠日级对账)。"""
    now = datetime.now(UTC)
    insert_call(conn, "t1", "NVDA", now)
    _, s1 = poll_new_calls(PollerState(), overlap_hours=6.0, conn=conn)
    insert_call(conn, "too_late", "TSLA", now - timedelta(hours=10))
    df2, _ = poll_new_calls(s1, overlap_hours=6.0, conn=conn)
    assert df2.is_empty()


def test_state_json_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "state.json"  # 目录自动创建
    ts = "2026-06-10T19:27:45+00:00"
    state = PollerState(last_seen_call_ts=ts, seen_recent={"t1|NVDA": ts})
    save_state(state, path)
    assert load_state(path) == state
    assert load_state(tmp_path / "missing.json") == PollerState()


def test_save_state_refuses_stock_picker_paths() -> None:
    """防参数转置:save_state 绝不写 stock-picker 侧(如误把 TRACKRECORD_DB 当 path)。"""
    for bad in (TRACKRECORD_DB, STOCK_PICKER_HOME / "x.json", Path.home() / "spm-web" / "exports" / "x.json"):
        with pytest.raises(ValueError, match="只读侧"):
            save_state(PollerState(), bad)


def test_malformed_call_ts_fails_loud(conn: sqlite3.Connection) -> None:
    """上游格式漂移('Z' 后缀 / naive)必须立刻炸,不允许静默坏水位。"""
    conn.execute(
        "INSERT INTO analyst_calls (tweet_id, ticker, direction, call_ts, is_call)"
        " VALUES ('tz', 'NVDA', 'bullish', '2026-06-10T10:00:00Z', 1)"
    )
    conn.commit()
    with pytest.raises(ValueError, match="格式违例"):
        poll_new_calls(PollerState(), conn=conn)
    conn.execute("UPDATE analyst_calls SET call_ts = '2026-06-10 10:00:00' WHERE tweet_id = 'tz'")
    conn.commit()
    with pytest.raises(ValueError, match="格式违例"):
        poll_new_calls(PollerState(), conn=conn)


def test_malformed_watermark_fails_loud(conn: sqlite3.Connection) -> None:
    """坏水位(naive 串)经 fromisoformat+astimezone 会按本机时区偏 7h——直接拒绝。"""
    with pytest.raises(ValueError, match="格式违例"):
        poll_new_calls(PollerState(last_seen_call_ts="2026-06-10 10:00:00"), conn=conn)


def test_seen_recent_pruned(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    insert_call(conn, "old", "NVDA", now - timedelta(hours=8))
    _, s1 = poll_new_calls(PollerState(), overlap_hours=6.0, conn=conn)
    assert "old|NVDA" in s1.seen_recent
    insert_call(conn, "new", "TSLA", now)  # 水位推进 8h,old 落到 overlap 外
    _, s2 = poll_new_calls(s1, overlap_hours=6.0, conn=conn)
    assert "old|NVDA" not in s2.seen_recent
    assert "new|TSLA" in s2.seen_recent


@pytest.mark.skipif(not TRACKRECORD_DB.exists(), reason="本地无 stock-picker trackrecord.db")
def test_real_db_fresh_poll_readonly() -> None:
    df, state = poll_new_calls(PollerState(), bootstrap_days=7.0)  # 默认 conn=connect_readonly
    assert df.columns == list(_CALL_COLUMNS)
    if df.height > 0:
        assert state.last_seen_call_ts == df.get_column("call_ts").max()
        assert df.get_column("direction").is_in(["bullish", "bearish"]).all()


@pytest.mark.skipif(
    not (TRACKRECORD_DB.exists() and TWEETS_DB.exists()), reason="本地无 stock-picker 两库"
)
def test_real_db_latency_measurement() -> None:
    stats = measure_ingest_latency(sample_days=14.0)
    assert stats["n"] > 0
    assert stats["p50_s"] <= stats["p90_s"] <= stats["p99_s"] <= stats["max_s"]
