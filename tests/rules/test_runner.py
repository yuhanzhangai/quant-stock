"""runner 集成测试:tmp DuckDB(复用 Data 管线 DDL)+ 合成 trackrecord sqlite + CRLF 诚实榜。

端到端口径与 tests/rules/test_engine.py 同一日历:决策时点 2026-06-09 15:30 ET。
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from src.rules.engine import PdtState
from src.rules.runner import fetch_recent_bearish, load_undecided, persist_decisions, run_decision_cycle
from src.signals.pipeline import _DDL as CANDIDATES_DDL
from tests.signals.test_calls_poller import SCHEMA_SQL

DECISION_TS = datetime(2026, 6, 9, 19, 30, tzinfo=UTC)
MON = datetime(2026, 6, 8, 14, 0, tzinfo=UTC)

_CSV_HEADER = (
    "handle,horizon,n,hit_rate,wilson_lo,wilson_hi,bull_n,bull_hit,bear_n,bear_hit,"
    "avg_dir_abret,span_days,earliest,latest,cross_regime,status"
)
_CSV_ROWS = [
    "alice,21d,30,0.7,0.52,0.83,20,0.75,10,0.6,0.045,120,2025-12-01,2026-03-31,True,PROVEN",
    "bob,21d,40,0.66,0.61,0.78,30,0.7,10,0.55,0.03,200,2025-10-01,2026-05-31,True,PROVEN",
    "dave,21d,8,0.62,0.31,0.86,5,0.6,3,0.67,0.012,40,2026-02-01,2026-03-13,False,TRACKING",
]


@pytest.fixture
def leaderboard_csv(tmp_path: Path) -> Path:
    path = tmp_path / "leaderboard_honest_2026-06-09.csv"
    path.write_bytes(("\r\n".join([_CSV_HEADER, *_CSV_ROWS]) + "\r\n").encode())
    return path


@pytest.fixture
def calls_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(tmp_path / "trackrecord.db")
    c.execute(SCHEMA_SQL)
    yield c
    c.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "out" / "signal_snapshots.duckdb"
    path.parent.mkdir(parents=True)
    con = duckdb.connect(str(path))
    con.execute(CANDIDATES_DDL)
    con.close()
    return path


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def insert_candidate(db: Path, sid: str, handle: str, ticker: str, call_ts: datetime,
                     direction: str = "bullish") -> None:
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "INSERT INTO signal_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [sid, f"tw_{sid}", handle, "a1", "PROVEN", date(2026, 6, 9), ticker, direction,
             call_ts, datetime.now(UTC), f"text {sid}", f"https://x/{sid}", call_ts, False, "high", 0.9],
        )
    finally:
        con.close()


def insert_bearish_call(c: sqlite3.Connection, tweet_id: str, handle: str, ticker: str,
                        ts: datetime) -> None:
    c.execute(
        "INSERT INTO analyst_calls (tweet_id, author_id, handle, ticker, direction, call_ts, call_date,"
        " is_call, confidence, conviction) VALUES (?, 'a1', ?, ?, 'bearish', ?, ?, 1, 0.8, 'high')",
        (tweet_id, handle, ticker, _iso(ts), ts.date().isoformat()),
    )
    c.commit()


def test_load_undecided_requires_candidates_table(tmp_path: Path):
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    with pytest.raises(RuntimeError, match="signal_candidates"):
        load_undecided(empty)


def test_fetch_recent_bearish_window_and_proven_filter(calls_conn: sqlite3.Connection):
    insert_bearish_call(calls_conn, "b1", "alice", "GOOGL", datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    insert_bearish_call(calls_conn, "b2", "alice", "TSLA", datetime(2026, 5, 20, 12, 0, tzinfo=UTC))  # 窗外
    insert_bearish_call(calls_conn, "b3", "dave", "NVDA", datetime(2026, 6, 8, 12, 0, tzinfo=UTC))    # 非 PROVEN
    df = fetch_recent_bearish(DECISION_TS, {"alice", "bob"}, conn=calls_conn)
    assert df.get_column("ticker").to_list() == ["GOOGL"]


def test_cycle_end_to_end_with_idempotent_rerun(db_path: Path, leaderboard_csv: Path,
                                                calls_conn: sqlite3.Connection):
    insert_candidate(db_path, "s_follow", "alice", "AAPL", MON)
    insert_candidate(db_path, "s_conflict", "bob", "GOOG", MON)        # alice 看空 GOOGL → issuer 冲突
    insert_candidate(db_path, "s_pending", "bob", "TSLA",
                     datetime(2026, 6, 9, 13, 0, tzinfo=UTC))          # T_entry=06-10,本轮不裁决
    insert_bearish_call(calls_conn, "b1", "alice", "GOOGL", datetime(2026, 6, 7, 12, 0, tzinfo=UTC))

    kw: dict = {"prices": {"AAPL": 50.0, "GOOG": 200.0, "TSLA": 300.0},
                "pdt": PdtState(0, 100_000.0), "db_path": db_path, "decision_ts": DECISION_TS,
                "leaderboard_path": leaderboard_csv, "calls_conn": calls_conn}
    stats = run_decision_cycle(**kw)
    assert stats == {"undecided": 3, "decided": 2, "followed": 1, "inserted": 2}

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = dict(con.execute("SELECT signal_id, decision_reason FROM rule_decisions").fetchall())
        versions = {r[0] for r in con.execute("SELECT DISTINCT rule_version FROM rule_decisions").fetchall()}
    finally:
        con.close()
    assert rows == {"s_follow": "all_gates_passed", "s_conflict": "direction_conflict"}
    assert versions == {"v0.1"}

    # 重跑:已决两行不重插、不改写;pending 仍未决(窗口到了才会出行)
    stats2 = run_decision_cycle(**kw)
    assert stats2 == {"undecided": 1, "decided": 0, "followed": 0, "inserted": 0}


def test_pending_decided_next_session(db_path: Path, leaderboard_csv: Path,
                                      calls_conn: sqlite3.Connection):
    insert_candidate(db_path, "s_pending", "bob", "TSLA", datetime(2026, 6, 9, 13, 0, tzinfo=UTC))
    kw: dict = {"prices": {"TSLA": 300.0}, "pdt": PdtState(0, 100_000.0), "db_path": db_path,
                "leaderboard_path": leaderboard_csv, "calls_conn": calls_conn}
    assert run_decision_cycle(decision_ts=DECISION_TS, **kw)["decided"] == 0
    stats = run_decision_cycle(decision_ts=datetime(2026, 6, 10, 19, 30, tzinfo=UTC), **kw)
    assert stats == {"undecided": 1, "decided": 1, "followed": 1, "inserted": 1}


def test_persist_decisions_never_overwrites(db_path: Path):
    import polars as pl

    from src.rules.engine import OUT_SCHEMA
    first = pl.DataFrame([("s1", "skipped", "signal_stale", "v0.1")], schema=OUT_SCHEMA, orient="row")
    again = pl.DataFrame([("s1", "followed", "all_gates_passed", "v0.1")], schema=OUT_SCHEMA, orient="row")
    assert persist_decisions(first, db_path) == 1
    assert persist_decisions(again, db_path) == 0  # 决策是终态,不可改写
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute("SELECT decision, decision_reason FROM rule_decisions WHERE signal_id='s1'").fetchall()
    finally:
        con.close()
    assert rows == [("skipped", "signal_stale")]
