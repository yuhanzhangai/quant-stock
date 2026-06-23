"""S 账批任务:signal_candidates × call_outcomes(21d)JOIN 消费 → 绩效快照(FOLLOW_PERF_SPEC §1/§3/§6)。

口径(spec §1.2):**JOIN 消费、不重算**——entry/exit/abnormal_return/is_hit 直接用 stock-picker
call_outcomes(只读),JOIN 键 (tweet_id, ticker, horizon_days=21);`pending` 不进汇总,`no_price`
单列计数,上游无行 = `unmatched`。死区(is_hit NULL)与上游一致,率值分母一律 graded_n,
graded_n<5 的单元格只报 n 不报率(echo 上游 MIN_CALLS_FOR_SCORE)。

延迟(v0,S 账侧仅有 ingest 段):ingest_lag = ingested_ts − call_ts,按 spec §3.2 wall 桶分桶;
两段拆解(Data 2026-06-10 口径)用 tweet_snapshots.fetched_at(上游入库 unix 秒):
upstream_lag = fetched_at − call_ts(大=上游深档回扫),poll_lag = ingested_ts − fetched_at(大=我方轮询)。
wall_latency / actionable_latency 需 orders.submitted_ts,待 E 账(P3)接入。

产物(spec §6):reports/follow_perf/<run_date>/{follow_perf.parquet, FOLLOW_PERF_REPORT.md, run_meta.json}。
纯读两库,产物可全量重算;报告按现行质量纪律自标「未经独立复核」。
"""

import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import polars as pl
from loguru import logger

from src.signals.paths import TRACKRECORD_DB, assert_writable_path
from src.signals.tweet_snapshot import DEFAULT_SNAPSHOT_DB

HORIZON_DAYS = 21
MIN_GRADED = 5  # echo 上游 MIN_CALLS_FOR_SCORE:graded_n<5 只报 n 不报率
REPORTS_ROOT = Path(__file__).resolve().parents[2] / "reports" / "follow_perf"

# spec §3.2 wall 桶(秒上界, 标签);v0 用于 ingest_lag,改桶=升版并在报告标注
_LAG_BUCKETS = ((7_200, "≤2h"), (21_600, "2–6h"), (86_400, "6–24h"), (259_200, "1–3d"), (math.inf, ">3d"))

_JOIN_SQL = f"""
SELECT
    s.signal_id, s.tweet_id, s.handle, s.tier, s.tier_csv_date, s.ticker, s.direction,
    s.call_ts, s.ingested_ts,
    coalesce(o.status, 'unmatched')                          AS outcome_status,
    o.entry_date, o.entry_close, o.exit_date, o.exit_close,
    o.fwd_return, o.benchmark_return, o.abnormal_return, o.is_hit,
    epoch(s.ingested_ts) - epoch(s.call_ts)                  AS ingest_lag_s,
    t.fetched_at - epoch(s.call_ts)                          AS upstream_lag_s,
    epoch(s.ingested_ts) - t.fetched_at                      AS poll_lag_s
FROM sig.signal_candidates s
LEFT JOIN tr.call_outcomes o
    ON o.tweet_id = s.tweet_id AND o.ticker = s.ticker AND o.horizon_days = {HORIZON_DAYS}
LEFT JOIN sig.tweet_snapshots t ON t.tweet_id = s.tweet_id
ORDER BY s.call_ts
"""


def wilson_lower(hits: int, n: int, z: float = 1.959963985) -> float:
    """Wilson score 95% 单侧下界(方法论同上游诚实榜)。n=0 返回 0.0。"""
    if n <= 0:
        return 0.0
    p = hits / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (center - margin) / denom


def lag_bucket(seconds: float | None) -> str | None:
    """ingest_lag 秒 → spec §3.2 桶标签;负值(时钟漂移/上游回填)单列 'anomaly'。"""
    if seconds is None:
        return None
    if seconds < 0:
        return "anomaly"
    for upper, label in _LAG_BUCKETS:
        if seconds <= upper:
            return label
    raise AssertionError("unreachable: 末桶上界为 inf")


def load_joined(snapshot_db: Path = DEFAULT_SNAPSHOT_DB, trackrecord_db: Path = TRACKRECORD_DB) -> pl.DataFrame:
    """两库只读 ATTACH,JOIN 出逐信号明细(S 账 + 延迟两段)。"""
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{trackrecord_db}' AS tr (TYPE sqlite, READ_ONLY)")
        con.execute(f"ATTACH '{snapshot_db}' AS sig (READ_ONLY)")
        joined = con.execute(_JOIN_SQL).pl()
    finally:
        con.close()
    return joined.with_columns(
        pl.col("ingest_lag_s").map_elements(lag_bucket, return_dtype=pl.String).alias("ingest_lag_bucket")
    )


def _rates(group: pl.DataFrame) -> dict[str, object]:
    """一个聚合单元的指标:n / graded_n / hit_rate+wilson / abnormal mean·median;graded_n<MIN_GRADED 率值置 None。"""
    evaluated = group.filter(pl.col("outcome_status") == "evaluated")
    graded = evaluated.filter(pl.col("is_hit").is_not_null())
    hits = int(graded.get_column("is_hit").sum() or 0)
    out: dict[str, object] = {
        "n": evaluated.height,
        "graded_n": graded.height,
        "hit_rate": None,
        "wilson_lo": None,
        "abn_mean": None,
        "abn_median": None,
    }
    if graded.height >= MIN_GRADED:
        out["hit_rate"] = hits / graded.height
        out["wilson_lo"] = wilson_lower(hits, graded.height)
    if evaluated.height >= MIN_GRADED:
        out["abn_mean"] = evaluated.get_column("abnormal_return").mean()
        out["abn_median"] = evaluated.get_column("abnormal_return").median()
    return out


def summarize(joined: pl.DataFrame) -> dict[str, object]:
    """漏斗 + 总体 + handle×tier + 延迟桶聚合(spec §3.3/§4 的 S 账子集)。"""
    funnel = dict(joined.group_by("outcome_status").len().iter_rows())
    by_handle = {f"{h}|{t}": _rates(g) for (h, t), g in joined.group_by(["handle", "tier"], maintain_order=True)}
    by_bucket = {str(b): _rates(g) for (b,), g in joined.group_by(["ingest_lag_bucket"], maintain_order=True)}
    lag = joined.select(
        pl.col("ingest_lag_s").quantile(0.5).alias("ingest_p50"),
        pl.col("ingest_lag_s").quantile(0.9).alias("ingest_p90"),
        pl.col("upstream_lag_s").quantile(0.5).alias("upstream_p50"),
        pl.col("upstream_lag_s").quantile(0.9).alias("upstream_p90"),
        pl.col("poll_lag_s").quantile(0.5).alias("poll_p50"),
        pl.col("poll_lag_s").quantile(0.9).alias("poll_p90"),
    ).row(0, named=True)
    return {
        "funnel": funnel,
        "overall": _rates(joined),
        "by_handle_tier": by_handle,
        "by_lag_bucket": by_bucket,
        "lag_quantiles": lag,
    }


def _fmt_rate(v: object) -> str:
    return f"{v:.1%}" if isinstance(v, float) else "—(graded_n<5)"


def _fmt_abn(v: object) -> str:
    return f"{v:+.2%}" if isinstance(v, float) else "—"


def _fmt_hours(v: object) -> str:
    return f"{v / 3600:.1f}h" if isinstance(v, (int, float)) else "—"


def render_report(summary: dict[str, object], meta: dict[str, object]) -> str:
    """人读 Markdown(spec §6);数字全部来自 summarize 产物,可由 parquet 复算。"""
    funnel: dict[str, int] = summary["funnel"]  # type: ignore[assignment]
    overall: dict[str, object] = summary["overall"]  # type: ignore[assignment]
    lagq: dict[str, object] = summary["lag_quantiles"]  # type: ignore[assignment]
    lines = [
        "# FOLLOW_PERF — S 账快照(signal / counterfactual)",
        "",
        f"> 生成 {meta['run_ts']} · code_commit `{meta['code_commit']}` · 复现:`{meta['command']}`",
        f"> 水位:call_ts {meta['watermark']} · tier_csv_date {meta['tier_csv_date_range']} · "
        f"horizon {HORIZON_DAYS} 交易日(口径=上游 call_outcomes,JOIN 消费不重算)",
        "> **未经独立复核**(强制审核制度 2026-06-10 废止,按新质量纪律自检后发布)",
        "",
        "## 漏斗",
        "",
        "| outcome_status | n | 说明 |",
        "|---|---|---|",
        f"| evaluated | {funnel.get('evaluated', 0)} | 进入汇总 |",
        f"| pending | {funnel.get('pending', 0)} | 21d 窗口未熟,不进任何汇总 |",
        f"| no_price | {funnel.get('no_price', 0)} | 上游无价格序列,S 账缺失单列 |",
        f"| unmatched | {funnel.get('unmatched', 0)} | call_outcomes 无对应行(上游尚未评估/缺失,日级对账跟踪)|",
        "",
        "## 总体(仅 evaluated;死区 is_hit=NULL 不计入分母)",
        "",
        "| n | graded_n | hit_rate | Wilson95 下界 | abnormal mean | abnormal median |",
        "|---|---|---|---|---|---|",
        f"| {overall['n']} | {overall['graded_n']} | {_fmt_rate(overall['hit_rate'])} "
        f"| {_fmt_rate(overall['wilson_lo'])} | {_fmt_abn(overall['abn_mean'])} | {_fmt_abn(overall['abn_median'])} |",
        "",
        "## handle × tier(graded_n<5 只报 n 不报率)",
        "",
        "| handle | tier | n | graded_n | hit_rate | Wilson95 下界 | abn mean |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, r in summary["by_handle_tier"].items():  # type: ignore[union-attr]
        handle, tier = key.split("|", 1)
        lines.append(
            f"| {handle} | {tier} | {r['n']} | {r['graded_n']} | {_fmt_rate(r['hit_rate'])} "
            f"| {_fmt_rate(r['wilson_lo'])} | {_fmt_abn(r['abn_mean'])} |"
        )
    lines += [
        "",
        "## ingest_lag 分桶(spec §3.2 桶;v0 仅 ingest 段,wall/actionable 待 E 账)",
        "",
        "| 桶 | n | graded_n | hit_rate | abn mean |",
        "|---|---|---|---|---|",
    ]
    for bucket, r in summary["by_lag_bucket"].items():  # type: ignore[union-attr]
        lines.append(
            f"| {bucket} | {r['n']} | {r['graded_n']} | {_fmt_rate(r['hit_rate'])} | {_fmt_abn(r['abn_mean'])} |"
        )
    lines += [
        "",
        "## 延迟两段拆解(p50 / p90)",
        "",
        "| 段 | p50 | p90 | 含义 |",
        "|---|---|---|---|",
        f"| ingest_lag(端到端)| {_fmt_hours(lagq['ingest_p50'])} | {_fmt_hours(lagq['ingest_p90'])} "
        "| call_ts→ingested_ts,上游+我方之和 |",
        f"| upstream_lag | {_fmt_hours(lagq['upstream_p50'])} | {_fmt_hours(lagq['upstream_p90'])} "
        "| call_ts→上游 fetched_at,日级大=上游深档回扫 |",
        f"| poll_lag | {_fmt_hours(lagq['poll_p50'])} | {_fmt_hours(lagq['poll_p90'])} "
        "| fetched_at→ingested_ts,大=我方轮询间隔 |",
        "",
        "## 已知局限(spec §5.3 + 本期)",
        "",
        "- **本期 ingested_ts 含管线首跑回填**:7d 窗口一次性补录,ingest/poll 段延迟偏大,不代表稳态;"
        "稳态数字以管线常驻轮询后的下期为准。",
        "- S 账为 counterfactual(上游 T+1 close 进出),非实际成交;E 账与 S−E 归因待 P3 真实成交接入。",
        "- 上游价格序列复权方式未核实;`pending` 行随上游评估推进迁移,重跑数字会变(活库)。",
        "- 绩效报告是观察记录,不是策略验证,不产生 PASS/上线判定(spec §5.4)。",
    ]
    return "\n".join(lines) + "\n"


def run(
    out_root: Path = REPORTS_ROOT, snapshot_db: Path = DEFAULT_SNAPSHOT_DB, trackrecord_db: Path = TRACKRECORD_DB
) -> Path:
    """跑一期 S 账快照,返回产物目录。纯读两库;产物目录按 run_date(UTC)分期。"""
    run_ts = datetime.now(UTC)
    joined = load_joined(snapshot_db, trackrecord_db)
    if joined.is_empty():
        raise RuntimeError(f"signal_candidates 为空:先跑 `uv run python -m src.signals.pipeline`(库:{snapshot_db})")
    summary = summarize(joined)
    code_commit = (
        subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            check=False,
        ).stdout.strip()
        or "unknown"
    )
    wm = joined.select(pl.col("call_ts").min().alias("lo"), pl.col("call_ts").max().alias("hi")).row(0)
    tcd = joined.select(pl.col("tier_csv_date").min().alias("lo"), pl.col("tier_csv_date").max().alias("hi")).row(0)
    meta: dict[str, object] = {
        "run_ts": run_ts.isoformat(timespec="seconds"),
        "code_commit": code_commit,
        "command": "uv run python -m src.perf.s_account",
        "snapshot_db": str(snapshot_db),
        "trackrecord_db": str(trackrecord_db),
        "horizon_days": HORIZON_DAYS,
        "candidates": joined.height,
        "watermark": f"{wm[0].isoformat()} → {wm[1].isoformat()}",
        "tier_csv_date_range": f"{tcd[0].isoformat()} → {tcd[1].isoformat()}",
        "review_status": "未经独立复核(强制审核制度 2026-06-10 废止)",
    }
    out_dir = assert_writable_path(out_root / run_ts.strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    joined.write_parquet(out_dir / "follow_perf.parquet")
    (out_dir / "FOLLOW_PERF_REPORT.md").write_text(render_report(summary, meta), encoding="utf-8")
    (out_dir / "run_meta.json").write_text(
        json.dumps(meta | {"funnel": summary["funnel"]}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("S 账快照完成: {} 信号 / 漏斗 {} / 产物 {}", joined.height, summary["funnel"], out_dir)
    return out_dir


if __name__ == "__main__":
    run()
