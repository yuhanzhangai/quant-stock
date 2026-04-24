"""数据质量检查。

每个检查返回统一格式:
{
    "check_name": str,
    "status": "pass" | "fail" | "warning",
    "severity": "critical" | "warning",
    "issue_count": int,
    "details": dict
}
"""

from dataclasses import dataclass

import polars as pl
from loguru import logger


@dataclass
class CheckResult:
    """数据质量检查结果。"""

    check_name: str
    status: str  # pass | fail | warning
    severity: str  # critical | warning
    issue_count: int
    details: dict

    def to_dict(self) -> dict:
        """转为字典。"""
        return {
            "check_name": self.check_name,
            "status": self.status,
            "severity": self.severity,
            "issue_count": self.issue_count,
            "details": self.details,
        }


def check_duplicate_timestamps(df: pl.DataFrame, ts_col: str = "timestamp") -> CheckResult:
    """检查重复时间戳。

    严重程度: critical — 回测拒绝运行。
    """
    if ts_col not in df.columns:
        return CheckResult("duplicate_timestamps", "pass", "critical", 0, {"note": f"column '{ts_col}' not found"})

    total = len(df)
    unique = df[ts_col].n_unique()
    duplicates = total - unique

    if duplicates > 0:
        dup_rows = df.group_by(ts_col).len().filter(pl.col("len") > 1)
        return CheckResult(
            "duplicate_timestamps",
            "fail",
            "critical",
            duplicates,
            {
                "total_rows": total,
                "unique_rows": unique,
                "duplicate_count": duplicates,
                "sample": str(dup_rows.head(5)),
            },
        )

    return CheckResult("duplicate_timestamps", "pass", "critical", 0, {"total_rows": total, "unique_rows": unique})


def check_timestamp_order(df: pl.DataFrame, ts_col: str = "timestamp") -> CheckResult:
    """检查时间戳是否单调递增。

    严重程度: critical。
    """
    if ts_col not in df.columns or len(df) < 2:
        return CheckResult("timestamp_order", "pass", "critical", 0, {})

    ts = df[ts_col]
    diffs = ts.diff().drop_nulls()
    non_increasing = (diffs <= 0).sum()

    if non_increasing > 0:
        return CheckResult(
            "timestamp_order",
            "fail",
            "critical",
            non_increasing,
            {"non_increasing_count": non_increasing},
        )

    return CheckResult("timestamp_order", "pass", "critical", 0, {})


def check_missing_bars(df: pl.DataFrame, timeframe: str, ts_col: str = "timestamp") -> CheckResult:
    """检查缺失 K 线。

    严重程度: warning（小缺口） / critical（大缺口）。
    """
    if ts_col not in df.columns or len(df) < 2:
        return CheckResult("missing_bars", "pass", "warning", 0, {})

    # 时间框架到毫秒映射
    tf_ms = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }

    interval = tf_ms.get(timeframe)
    if not interval:
        return CheckResult("missing_bars", "pass", "warning", 0, {"note": f"unknown timeframe: {timeframe}"})

    ts = df[ts_col].cast(pl.Int64)
    diffs = ts.diff().drop_nulls()

    # 计算间隔大于预期的数量
    gaps = diffs.filter(diffs > interval * 1.5)  # 1.5x tolerance
    gap_count = len(gaps)

    if gap_count == 0:
        return CheckResult("missing_bars", "pass", "warning", 0, {"expected_interval_ms": interval})

    # 估算缺失根数
    total_missing = 0
    large_gaps = 0
    for g in gaps.to_list():
        missing = round(g / interval) - 1
        total_missing += missing
        if missing > 10:
            large_gaps += 1

    total_bars = len(df)
    missing_pct = total_missing / (total_bars + total_missing) * 100 if total_bars > 0 else 0

    # 大缺口 (>5% 缺失) = critical
    severity = "critical" if missing_pct > 5 else "warning"
    status = "fail" if severity == "critical" else "warning"

    return CheckResult(
        "missing_bars",
        status,
        severity,
        total_missing,
        {
            "total_bars": total_bars,
            "gap_events": gap_count,
            "estimated_missing": total_missing,
            "missing_pct": round(missing_pct, 2),
            "large_gaps": large_gaps,
        },
    )


def check_ohlc_validity(df: pl.DataFrame) -> CheckResult:
    """检查 OHLC 数据合法性。

    规则:
    - high >= open, close, low
    - low <= open, close
    - all prices > 0

    严重程度: critical。
    """
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return CheckResult("ohlc_validity", "pass", "critical", 0, {"note": "missing OHLC columns"})

    issues = 0
    details = {}

    # Price > 0
    for col in ["open", "high", "low", "close"]:
        non_positive = (df[col] <= 0).sum()
        if non_positive > 0:
            issues += non_positive
            details[f"{col}_non_positive"] = non_positive

    # high >= open, close, low
    high_lt_open = (df["high"] < df["open"]).sum()
    high_lt_close = (df["high"] < df["close"]).sum()
    high_lt_low = (df["high"] < df["low"]).sum()

    if high_lt_open > 0:
        issues += high_lt_open
        details["high_lt_open"] = high_lt_open
    if high_lt_close > 0:
        issues += high_lt_close
        details["high_lt_close"] = high_lt_close
    if high_lt_low > 0:
        issues += high_lt_low
        details["high_lt_low"] = high_lt_low

    # low <= open, close
    low_gt_open = (df["low"] > df["open"]).sum()
    low_gt_close = (df["low"] > df["close"]).sum()

    if low_gt_open > 0:
        issues += low_gt_open
        details["low_gt_open"] = low_gt_open
    if low_gt_close > 0:
        issues += low_gt_close
        details["low_gt_close"] = low_gt_close

    status = "fail" if issues > 0 else "pass"
    return CheckResult("ohlc_validity", status, "critical", issues, details)


def check_volume_validity(df: pl.DataFrame) -> CheckResult:
    """检查成交量合法性。

    规则: volume >= 0, not null。
    严重程度: warning。
    """
    if "volume" not in df.columns:
        return CheckResult("volume_validity", "pass", "warning", 0, {"note": "no volume column"})

    null_count = df["volume"].null_count()
    negative = (df["volume"] < 0).sum()
    zero_count = (df["volume"] == 0).sum()
    issues = null_count + negative

    details = {"null_count": null_count, "negative_count": negative, "zero_count": zero_count}

    if issues > 0:
        return CheckResult("volume_validity", "warning", "warning", issues, details)

    return CheckResult("volume_validity", "pass", "warning", 0, details)


def check_price_jump(df: pl.DataFrame, threshold_pct: float = 30.0) -> CheckResult:
    """检查异常跳价。

    单根 K 线 close 变化超过 threshold_pct 视为异常。
    不一定是错误（可能是黑天鹅），但必须标记。

    严重程度: warning。
    """
    if "close" not in df.columns or len(df) < 2:
        return CheckResult("price_jump", "pass", "warning", 0, {})

    pct_change = df["close"].pct_change().abs() * 100
    jumps = pct_change.filter(pct_change > threshold_pct).drop_nulls()
    jump_count = len(jumps)

    if jump_count > 0:
        return CheckResult(
            "price_jump",
            "warning",
            "warning",
            jump_count,
            {
                "threshold_pct": threshold_pct,
                "jump_count": jump_count,
                "max_jump_pct": round(float(jumps.max()), 2),
            },
        )

    return CheckResult("price_jump", "pass", "warning", 0, {"threshold_pct": threshold_pct})


def check_latest_bar_delay(
    df: pl.DataFrame, timeframe: str, ts_col: str = "timestamp", max_delay_bars: int = 2
) -> CheckResult:
    """检查最新数据延迟。

    当前时间 - 最新K线时间 > max_delay_bars 个 timeframe。

    严重程度: warning (research) / critical (live signal)。
    """
    import time

    if ts_col not in df.columns or len(df) == 0:
        return CheckResult("latest_bar_delay", "pass", "warning", 0, {})

    tf_ms = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }

    interval = tf_ms.get(timeframe)
    if not interval:
        return CheckResult("latest_bar_delay", "pass", "warning", 0, {"note": f"unknown timeframe: {timeframe}"})

    latest_ts = int(df[ts_col].max())
    now_ms = int(time.time() * 1000)
    delay_ms = now_ms - latest_ts
    delay_bars = delay_ms / interval

    if delay_bars > max_delay_bars:
        return CheckResult(
            "latest_bar_delay",
            "warning",
            "warning",
            1,
            {
                "delay_bars": round(delay_bars, 1),
                "delay_hours": round(delay_ms / 3_600_000, 1),
                "max_delay_bars": max_delay_bars,
            },
        )

    return CheckResult(
        "latest_bar_delay",
        "pass",
        "warning",
        0,
        {"delay_bars": round(delay_bars, 1)},
    )


def run_all_checks(df: pl.DataFrame, timeframe: str = "5m", ts_col: str = "timestamp") -> list[CheckResult]:
    """运行所有数据质量检查。"""
    results = [
        check_duplicate_timestamps(df, ts_col),
        check_timestamp_order(df, ts_col),
        check_missing_bars(df, timeframe, ts_col),
        check_ohlc_validity(df),
        check_volume_validity(df),
        check_price_jump(df),
        check_latest_bar_delay(df, timeframe, ts_col),
    ]

    for r in results:
        level = "error" if r.status == "fail" else ("warning" if r.status == "warning" else "debug")
        getattr(logger, level)(f"  [{r.status.upper():7s}] {r.check_name} | issues={r.issue_count}")

    return results


def has_critical_failure(results: list[CheckResult]) -> bool:
    """检查是否有 critical 失败。"""
    return any(r.status == "fail" and r.severity == "critical" for r in results)
