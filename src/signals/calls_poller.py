"""analyst_calls 增量轮询:call_ts 事件时间水位 + overlap 回看 + (tweet_id,ticker) 去重。

爬虫"发帖 → 入库"有延迟,纯水位会把迟到入库的行永久漏掉,所以每轮回看
last_seen - overlap_hours,再用 seen_recent 去重只返回真正新行。

overlap 取值依据(2026-06-10 实测,`uv run python -m src.signals.calls_poller`):
稳态(剔除 06-05~06-07 爬虫启动期回填)n=260,延迟 p50≈45min、p90≈1.5h、p95≈3.3h;
overlap=6h 覆盖 97.3%、24h 覆盖 98.5%。残余 ~1.5% 是深档回扫(几天前的旧帖被新抓到,
延迟 4.8~9.8 天),overlap 结构上覆盖不了——需另跑日级对账(bootstrap 窗口重扫)兜底。
超出 overlap 的迟到行会漏是已知取舍。stock-picker 两库一律只读。
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
from loguru import logger

from src.signals.paths import (
    SIGNALS_DATA_DIR,
    TRACKRECORD_DB,
    TWEETS_DB,
    assert_writable_path,
    connect_readonly,
)

DEFAULT_STATE_PATH = SIGNALS_DATA_DIR / "calls_poller_state.json"

_CALL_COLUMNS = ("tweet_id", "handle", "author_id", "ticker", "direction", "call_ts", "call_date",
                 "confidence", "conviction")
_TS_IDX = _CALL_COLUMNS.index("call_ts")
_CALL_SCHEMA = pl.Schema({
    "tweet_id": pl.String, "handle": pl.String, "author_id": pl.String, "ticker": pl.String,
    "direction": pl.String, "call_ts": pl.String, "call_date": pl.String,
    "confidence": pl.Float64, "conviction": pl.String,
})


@dataclass
class PollerState:
    """轮询水位状态:last_seen_call_ts 单调不回退;seen_recent key='tweet_id|ticker'。"""

    last_seen_call_ts: str | None = None
    seen_recent: dict[str, str] = field(default_factory=dict)


def load_state(path: Path = DEFAULT_STATE_PATH) -> PollerState:
    """从 JSON 读状态;文件不存在返回全新状态(触发 bootstrap)。"""
    if not path.exists():
        return PollerState()
    raw = json.loads(path.read_text())
    return PollerState(last_seen_call_ts=raw.get("last_seen_call_ts"), seen_recent=raw.get("seen_recent", {}))


def save_state(state: PollerState, path: Path = DEFAULT_STATE_PATH) -> None:
    """状态写 JSON,目录不存在自动建(只写本仓 data/signals/ 或测试 tmp)。"""
    assert_writable_path(path)  # 防参数转置:绝不覆写 stock-picker 侧文件
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_seen_call_ts": state.last_seen_call_ts, "seen_recent": state.seen_recent}
    tmp = path.with_name(path.name + ".tmp")  # 原子替换:半途被杀不留截断 JSON
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def _iso(dt: datetime) -> str:
    """统一成 DB 同款格式(秒级、+00:00),保证字符串比较 == 时间比较。"""
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _assert_utc_iso(ts: object, source: str) -> str:
    """call_ts/水位格式断言:必须 'YYYY-MM-DDTHH:MM:SS+00:00'(len 25)。

    水位靠字符串字典序比较,混入 'Z' 后缀或 naive 格式会静默失真
    (naive 串经 fromisoformat 再 astimezone 按本机时区偏移)——违例直接 fail loud。
    """
    if not isinstance(ts, str) or len(ts) != 25 or not ts.endswith("+00:00"):
        raise ValueError(f"{source} call_ts 格式违例(需 'YYYY-MM-DDTHH:MM:SS+00:00'): {ts!r}")
    return ts


def poll_new_calls(
    state: PollerState,
    overlap_hours: float = 6.0,
    bootstrap_days: float = 7.0,
    conn: sqlite3.Connection | None = None,
) -> tuple[pl.DataFrame, PollerState]:
    """增量拉取 is_call=1 新行。返回 (新行 DataFrame, 新状态);对库只读,不修改入参 state。"""
    if state.last_seen_call_ts is None:
        since = _iso(datetime.now(UTC) - timedelta(days=bootstrap_days))
    else:
        wm = _assert_utc_iso(state.last_seen_call_ts, "state.last_seen_call_ts")
        since = _iso(datetime.fromisoformat(wm) - timedelta(hours=overlap_hours))
    owned = conn is None
    if conn is None:
        conn = connect_readonly(TRACKRECORD_DB)
    try:
        sql = (f"SELECT {', '.join(_CALL_COLUMNS)} FROM analyst_calls "
               f"WHERE is_call = 1 AND call_ts > ? ORDER BY call_ts")
        rows = conn.execute(sql, (since,)).fetchall()
    finally:
        if owned:
            conn.close()

    for r in rows:  # 上游格式漂移即刻暴露,绝不让坏水位静默入状态
        _assert_utc_iso(r[_TS_IDX], f"analyst_calls tweet_id={r[0]}")
    fresh = [r for r in rows if f"{r[0]}|{r[3]}" not in state.seen_recent]
    df = pl.DataFrame(fresh, schema=_CALL_SCHEMA, orient="row")

    watermark = state.last_seen_call_ts
    seen = dict(state.seen_recent)
    if fresh:
        batch_max = max(r[_TS_IDX] for r in fresh)
        # 只来迟到行时 batch_max < 旧水位,水位保持单调不回退
        watermark = batch_max if watermark is None or batch_max > watermark else watermark
        for r in fresh:
            seen[f"{r[0]}|{r[3]}"] = r[_TS_IDX]
    if watermark is not None:
        cutoff = _iso(datetime.fromisoformat(watermark) - timedelta(hours=overlap_hours))
        seen = {k: v for k, v in seen.items() if v >= cutoff}
    return df, PollerState(last_seen_call_ts=watermark, seen_recent=seen)


def measure_ingest_latency(sample_days: float = 14.0) -> dict[str, float | int]:
    """实测发帖→入库延迟:近 N 天 is_call=1 的 distinct tweet_id JOIN tweets,
    latency = fetched_at - epoch(created_at)。两库各拉一个 frame 后 polars join(不 ATTACH)。"""
    since = _iso(datetime.now(UTC) - timedelta(days=sample_days))
    with closing(connect_readonly(TRACKRECORD_DB)) as c:
        ids = [r[0] for r in c.execute(
            "SELECT DISTINCT tweet_id FROM analyst_calls WHERE is_call = 1 AND call_ts > ?", (since,))]
    with closing(connect_readonly(TWEETS_DB)) as c:
        tw_rows = c.execute("SELECT id, created_at, fetched_at FROM tweets").fetchall()
    calls = pl.DataFrame({"tweet_id": ids}, schema={"tweet_id": pl.String})
    tweets = pl.DataFrame(tw_rows, schema={"tweet_id": pl.String, "created_at": pl.String, "fetched_at": pl.Int64},
                          orient="row")
    joined = calls.join(tweets, on="tweet_id", how="inner").with_columns(
        (pl.col("fetched_at") - pl.col("created_at").str.to_datetime(time_zone="UTC").dt.epoch(time_unit="s"))
        .alias("latency_s"))
    if joined.is_empty():
        raise RuntimeError(f"近 {sample_days} 天无可 JOIN 的 call/tweet 样本")
    lat = joined.get_column("latency_s")
    return {
        "n": joined.height,
        "p50_s": float(lat.quantile(0.5) or 0.0),
        "p90_s": float(lat.quantile(0.9) or 0.0),
        "p99_s": float(lat.quantile(0.99) or 0.0),
        "max_s": float(lat.max() or 0.0),  # type: ignore[arg-type]
        "min_s": float(lat.min() or 0.0),  # type: ignore[arg-type]
        "neg_n": int((lat < 0).sum()),
        "orphan_n": len(ids) - joined.height,
    }


if __name__ == "__main__":
    stats = measure_ingest_latency()
    logger.info("ingest latency 14d: n={n} p50={p50_s:.0f}s p90={p90_s:.0f}s p99={p99_s:.0f}s "
                "max={max_s:.0f}s min={min_s:.0f}s neg_n={neg_n} orphan_n={orphan_n}", **stats)
    logger.info("建议 overlap_hours >= p99 延迟 = {:.2f}h(再留余量)", stats["p99_s"] / 3600)
