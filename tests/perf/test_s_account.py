"""S 账批任务测试:纯函数口径 + 合成双库(duckdb 候选 / sqlite call_outcomes)全链路。

线上候选全部 pending(窗口未熟),evaluated 分支只能靠合成数据覆盖——
deadzone(is_hit NULL)分母、graded_n<5 压率、pending/unmatched 漏斗、延迟两段都在这里钉死。
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from src.perf.s_account import lag_bucket, load_joined, run, summarize, wilson_lower

_CALL_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_EPOCH = int(_CALL_TS.timestamp())


def test_wilson_lower_known_values():
    assert wilson_lower(0, 0) == 0.0
    assert wilson_lower(50, 100) == pytest.approx(0.4038, abs=2e-3)  # p=0.5, n=100 经典值
    assert wilson_lower(100, 100) < 1.0  # 全中也不许报 1.0
    assert wilson_lower(0, 100) == pytest.approx(0.0, abs=1e-6)


def test_lag_bucket_boundaries():
    assert lag_bucket(None) is None
    assert lag_bucket(-5) == "anomaly"  # 时钟漂移/回填异常单列,不混入正常桶
    assert lag_bucket(0) == "≤2h"
    assert lag_bucket(7_200) == "≤2h"
    assert lag_bucket(7_201) == "2–6h"
    assert lag_bucket(86_400) == "6–24h"
    assert lag_bucket(259_201) == ">3d"


@pytest.fixture()
def synthetic_dbs(tmp_path: Path) -> tuple[Path, Path]:
    """6 信号:4 evaluated(3 hit + 1 deadzone)+ 1 pending + 1 unmatched;tweet_snapshots 提供 fetched_at。"""
    sig_db = tmp_path / "sig.duckdb"
    tr_db = tmp_path / "tr.db"

    con = duckdb.connect(str(sig_db))
    con.execute("""CREATE TABLE signal_candidates (
        signal_id TEXT, tweet_id TEXT, handle TEXT, tier TEXT, tier_csv_date DATE,
        ticker TEXT, direction TEXT, call_ts TIMESTAMPTZ, ingested_ts TIMESTAMPTZ)""")
    con.execute("CREATE TABLE tweet_snapshots (tweet_id TEXT, fetched_at BIGINT)")
    rows = [(f"sig_t{i}_AAA", f"t{i}", "shay", "PROVEN", "2026-06-01", "AAA", "bullish") for i in range(6)]
    for i, r in enumerate(rows):
        # ingest_lag: 信号 0-4 = 1h(≤2h 桶),信号 5 = 30h(1–3d 桶);fetched_at 统一 call+10min
        lag_s = 3_600 if i < 5 else 108_000
        con.execute("INSERT INTO signal_candidates VALUES (?,?,?,?,?,?,?, to_timestamp(?), to_timestamp(?))",
                    [*r, _EPOCH, _EPOCH + lag_s])
        con.execute("INSERT INTO tweet_snapshots VALUES (?, ?)", [r[1], _EPOCH + 600])
    con.close()

    tr = sqlite3.connect(tr_db)
    tr.execute("""CREATE TABLE call_outcomes (
        tweet_id TEXT, ticker TEXT, horizon_days INTEGER, entry_date TEXT, entry_close REAL,
        exit_date TEXT, exit_close REAL, fwd_return REAL, benchmark_return REAL,
        abnormal_return REAL, is_hit INTEGER, status TEXT)""")
    outcome = [
        ("t0", 0.10, 0.02, 0.08, 1, "evaluated"),
        ("t1", 0.05, 0.02, 0.03, 1, "evaluated"),
        ("t2", -0.04, 0.02, -0.06, 0, "evaluated"),
        ("t3", 0.021, 0.02, 0.001, None, "evaluated"),  # 死区:|abnormal|<0.5% → is_hit NULL
        ("t4", None, None, None, None, "pending"),
        # t5 无行 → unmatched
    ]
    for tid, fwd, bench, abn, hit, status in outcome:
        tr.execute("INSERT INTO call_outcomes VALUES (?,?,21,'2026-05-02',100,'2026-06-02',110,?,?,?,?,?)",
                   (tid, "AAA", fwd, bench, abn, hit, status))
    # 干扰行:同 tweet 的 1d horizon,JOIN 必须只取 21
    tr.execute("INSERT INTO call_outcomes VALUES "
               "('t0','AAA',1,'2026-05-02',100,'2026-05-03',99,-0.01,0,-0.01,0,'evaluated')")
    tr.commit()
    tr.close()
    return sig_db, tr_db


def test_join_funnel_and_lag_segments(synthetic_dbs: tuple[Path, Path]):
    joined = load_joined(*synthetic_dbs)
    assert joined.height == 6  # horizon=1 干扰行没造成扇出
    funnel = dict(joined.group_by("outcome_status").len().iter_rows())
    assert funnel == {"evaluated": 4, "pending": 1, "unmatched": 1}
    row = joined.filter(joined["tweet_id"] == "t0").row(0, named=True)
    assert row["ingest_lag_s"] == 3_600
    assert row["upstream_lag_s"] == 600  # call→fetched_at
    assert row["poll_lag_s"] == 3_000  # fetched_at→ingested_ts
    assert row["ingest_lag_bucket"] == "≤2h"


def test_summarize_deadzone_denominator_and_suppression(synthetic_dbs: tuple[Path, Path]):
    summary = summarize(load_joined(*synthetic_dbs))
    overall = summary["overall"]
    # evaluated=4,graded=3(死区 t3 出分母):3 graded < MIN_GRADED=5 → 率值压掉只报 n
    assert overall["n"] == 4 and overall["graded_n"] == 3
    assert overall["hit_rate"] is None and overall["wilson_lo"] is None and overall["abn_mean"] is None
    assert summary["by_lag_bucket"]["≤2h"]["n"] == 4  # pending/unmatched 不计入 evaluated n
    assert summary["by_lag_bucket"]["1–3d"]["n"] == 0


def test_summarize_reports_rates_when_enough_graded(synthetic_dbs: tuple[Path, Path]):
    base = load_joined(*synthetic_dbs)
    evaluated = base.filter(base["outcome_status"] == "evaluated")
    inflated = evaluated.vstack(evaluated).vstack(evaluated)  # graded 3→9 ≥ MIN_GRADED
    overall = summarize(inflated)["overall"]
    assert overall["graded_n"] == 9
    assert overall["hit_rate"] == pytest.approx(6 / 9)
    assert 0.3 < overall["wilson_lo"] < 6 / 9  # 下界必须低于点估计
    assert overall["abn_mean"] == pytest.approx((0.08 + 0.03 - 0.06 + 0.001) * 3 / 12)


def test_run_writes_three_artifacts(synthetic_dbs: tuple[Path, Path], tmp_path: Path):
    sig_db, tr_db = synthetic_dbs
    out_dir = run(out_root=tmp_path / "follow_perf", snapshot_db=sig_db, trackrecord_db=tr_db)
    report = (out_dir / "FOLLOW_PERF_REPORT.md").read_text(encoding="utf-8")
    meta = json.loads((out_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert (out_dir / "follow_perf.parquet").exists()
    assert "未经独立复核" in report and "graded_n<5" in report
    assert meta["funnel"] == {"evaluated": 4, "pending": 1, "unmatched": 1}
    assert meta["horizon_days"] == 21
