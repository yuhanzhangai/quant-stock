"""诚实榜 CSV 读取器(stock-picker 导出,只读)。

CSV 为 CRLF 行尾,所有字符串列读后 strip 防 \r 残留;status 精确等值匹配 PROVEN
(前缀匹配会误收 PROVEN_1REGIME / PROVEN_BAD_1REGIME)。
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import polars as pl
from loguru import logger

from src.signals.paths import LEADERBOARD_EXPORT_DIRS

_CSV_PREFIX = "leaderboard_honest_"
_INT_COLS = ("n", "bull_n", "bear_n", "span_days")
_FLOAT_COLS = ("hit_rate", "wilson_lo", "wilson_hi", "bull_hit", "bear_hit", "avg_dir_abret")
_BOOL_COLS = ("cross_regime",)


def _csv_date(path: Path) -> date | None:
    """从文件名 leaderboard_honest_<YYYY-MM-DD>.csv 提取日期;不合法返回 None。"""
    try:
        return date.fromisoformat(path.stem.removeprefix(_CSV_PREFIX))
    except ValueError:
        return None


def discover_latest_csv(export_dirs: Sequence[Path] = LEADERBOARD_EXPORT_DIRS) -> Path:
    """按目录顺序探测诚实榜 CSV,取文件名日期最大者(同日取靠前目录)。"""
    best: tuple[date, Path] | None = None
    for export_dir in export_dirs:
        if not export_dir.is_dir():
            continue
        for path in sorted(export_dir.glob(f"{_CSV_PREFIX}*.csv")):
            csv_date = _csv_date(path)
            # 只在日期严格更大时替换:同日时先扫到的(靠前目录)保留
            if csv_date is not None and (best is None or csv_date > best[0]):
                best = (csv_date, path)
    if best is None:
        raise FileNotFoundError(f"no {_CSV_PREFIX}<YYYY-MM-DD>.csv found in {[str(d) for d in export_dirs]}")
    logger.debug("discovered latest honest leaderboard CSV: {}", best[1])
    return best[1]


def load_leaderboard(path: Path | None = None) -> pl.DataFrame:
    """读诚实榜 CSV(默认最新):全列 strip 去 CRLF 残留,数值/布尔列正确类型。"""
    if path is None:
        path = discover_latest_csv()
    df = pl.read_csv(path, infer_schema_length=0)  # 全部按 utf8 读,strip 后再类型化
    df = df.with_columns(pl.all().str.strip_chars().replace("", None))
    return df.with_columns(
        [pl.col(c).cast(pl.Int64) for c in _INT_COLS]
        + [pl.col(c).cast(pl.Float64) for c in _FLOAT_COLS]
        + [(pl.col(c).str.to_lowercase() == "true").fill_null(False).alias(c) for c in _BOOL_COLS]
    )


def proven(horizon: str = "21d", path: Path | None = None) -> pl.DataFrame:
    """status 精确 == 'PROVEN' 且 horizon 精确匹配的子集。"""
    df = load_leaderboard(path)
    return df.filter((pl.col("status") == "PROVEN") & (pl.col("horizon") == horizon))


def proven_handles(horizon: str = "21d", path: Path | None = None) -> list[str]:
    """PROVEN 博主 handle 列表(指定 horizon)。"""
    return proven(horizon, path)["handle"].to_list()
