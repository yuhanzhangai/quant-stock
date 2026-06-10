"""诚实榜 CSV 读取器测试:合成 CRLF fixture + 真实文件(只读,skipif 守护)。"""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from src.signals.honest_leaderboard import _csv_date, discover_latest_csv, load_leaderboard, proven, proven_handles
from src.signals.paths import LEADERBOARD_EXPORT_DIRS

_HEADER = (
    "handle,horizon,n,hit_rate,wilson_lo,wilson_hi,bull_n,bull_hit,bear_n,bear_hit,"
    "avg_dir_abret,span_days,earliest,latest,cross_regime,status"
)
_ROWS = [
    "alice,21d,30,0.7,0.52,0.83,20,0.75,10,0.6,0.045,120,2025-12-01,2026-03-31,True,PROVEN",
    "bob,21d,25,0.68,0.48,0.82,25,0.68,0,,0.031,90,2026-01-01,2026-04-01,False,PROVEN_1REGIME",
    "carol,21d,22,0.3,0.15,0.5,22,0.3,0,,-0.02,80,2026-01-05,2026-03-26,False,PROVEN_BAD_1REGIME",
    "dave,21d,8,0.62,0.31,0.86,5,0.6,3,0.67,0.012,40,2026-02-01,2026-03-13,False,TRACKING",
    "erin,21d,1,1.0,0.207,1.0,1,1.0,0,,0.08,0,2026-03-01,2026-03-01,False,INSUFFICIENT",
    "frank,5d,40,0.72,0.56,0.84,30,0.7,10,0.8,0.02,150,2025-11-01,2026-03-31,True,PROVEN",
    "grace,21d,35,0.2,0.1,0.36,35,0.2,0,,-0.05,130,2025-12-01,2026-04-10,True,PROVEN_BAD",
]

_REAL_AVAILABLE = any(d.is_dir() and any(d.glob("leaderboard_honest_*.csv")) for d in LEADERBOARD_EXPORT_DIRS)
_skip_no_real = pytest.mark.skipif(not _REAL_AVAILABLE, reason="real leaderboard CSV not available locally")


@pytest.fixture
def crlf_csv(tmp_path: Path) -> Path:
    """手工拼 CRLF(\\r\\n)的小 CSV,模拟真实导出的行尾。"""
    path = tmp_path / "leaderboard_honest_2026-06-01.csv"
    path.write_bytes(("\r\n".join([_HEADER, *_ROWS]) + "\r\n").encode())
    return path


def test_load_strips_crlf_and_types(crlf_csv: Path) -> None:
    df = load_leaderboard(crlf_csv)
    assert df.height == 7
    assert not any("\r" in s for s in df["status"].to_list()), "status 列残留 \\r"
    assert df["n"].dtype == pl.Int64
    assert df["span_days"].dtype == pl.Int64
    assert df["hit_rate"].dtype == pl.Float64
    assert df["avg_dir_abret"].dtype == pl.Float64
    assert df["cross_regime"].dtype == pl.Boolean
    assert df["cross_regime"].to_list() == [True, False, False, False, False, True, True]
    assert df["bear_hit"].to_list()[1] is None  # 空字段 → null,不是空串


def test_proven_exact_match_only(crlf_csv: Path) -> None:
    df = proven("21d", path=crlf_csv)
    assert df["handle"].to_list() == ["alice"], "前缀匹配会误收 PROVEN_1REGIME/PROVEN_BAD*/..."
    assert proven_handles("21d", path=crlf_csv) == ["alice"]
    assert proven_handles("5d", path=crlf_csv) == ["frank"]  # horizon 也是精确匹配


def test_discover_latest_across_dirs(tmp_path: Path) -> None:
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "leaderboard_honest_2026-06-08.csv").write_text("x")
    (dir_b / "leaderboard_honest_2026-06-09.csv").write_text("x")
    assert discover_latest_csv((dir_a, dir_b)).name == "leaderboard_honest_2026-06-09.csv"
    # 同日:取靠前目录
    (dir_a / "leaderboard_honest_2026-06-09.csv").write_text("x")
    assert discover_latest_csv((dir_a, dir_b)).parent == dir_a
    # 不合法文件名忽略;找不到抛 FileNotFoundError
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "leaderboard_honest_garbage.csv").write_text("x")
    with pytest.raises(FileNotFoundError):
        discover_latest_csv((empty, tmp_path / "missing"))


@_skip_no_real
def test_real_discover_recent() -> None:
    path = discover_latest_csv()
    csv_date = _csv_date(path)
    # 相对容差而非硬编码日期:导出停更几天不该把单元测试搞红(新鲜度告警归监控/对账)
    assert csv_date is not None and (date.today() - csv_date).days <= 7


@_skip_no_real
def test_real_proven_21d() -> None:
    df = proven("21d")
    assert df.height > 0
    full = load_leaderboard()
    statuses = full["status"].to_list()
    assert not any("\r" in s for s in statuses)
    # PROVEN_BAD 是实测发现的状态值(文档原本未列):2026-06-10 真实 CSV 有 19 行
    known = {"PROVEN", "PROVEN_BAD", "TRACKING", "FADE", "INSUFFICIENT", "PROVEN_1REGIME", "PROVEN_BAD_1REGIME"}
    assert set(statuses) <= known
