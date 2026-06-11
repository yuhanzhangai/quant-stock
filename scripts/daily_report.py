"""模拟盘每日一屏汇报(operator 15:00 PT 看)— 写 logs/daily_report.md + stdout。

数据面 = ledger parquet 导出(复用 dashboard/ledger_reader,永不直连 ledger.duckdb)。
内容:当日 runner 心跳/退出码、当前持仓与浮动盈亏(未实现口径)、当日建仓平仓、
异常(心跳断/价源停/崩溃/对账 mismatch/kill-switch/导出陈旧)、一句话结论。
空账本/无导出 → 优雅降级("观察期早期,持仓积累中"),不报错。

退出码从 agent_runs 重建(runner 不落码):当日无行=没跑到记账(launchd 未跑或
exit-3 早退:信号源缺/价源全 missing);error 非空=3(停);正常收尾=0 或 2
(两者 ledger 内不可区分,2=部分缺价已逐票 skip,精确值见 runner 日志)。

用法:uv run python scripts/daily_report.py [--date YYYY-MM-DD](默认今日 ET)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from loguru import logger

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "dashboard"))

from config.settings import get_settings  # noqa: E402

os.environ.setdefault("LEDGER_EXPORT_DIR", str(get_settings().execution_export_dir))

import ledger_reader as lr  # noqa: E402

_ET = ZoneInfo("America/New_York")
REPORT_PATH = _ROOT / "logs" / "daily_report.md"
HEADER_NOTE = "PAPER 模拟盘(本地按真价模拟,无真金)· 盈亏均为**未实现**口径 · 观察期早期 · 未经独立复核"


def _et_date(ts: pd.Timestamp) -> date:
    return ts.tz_convert(_ET).date() if ts.tzinfo else ts.tz_localize(UTC).tz_convert(_ET).date()


def _runner_section(runs: pd.DataFrame, d: date, issues: list[str]) -> list[str]:
    trading_day = d.weekday() < 5  # 周末肯定非交易日;节假日口径以 runner 日志为准
    today = runs[runs["started_ts"].map(_et_date) == d] if not runs.empty else runs
    if today.empty:
        if trading_day:
            issues.append("当日无 runner 心跳——launchd 未跑,或 exit-3 早退(信号源缺/价源全 missing,查 runner 日志)")
            return ["- 本日心跳:**无**(异常,见下)"]
        return ["- 本日心跳:无(周末/非交易日,正常)"]
    r = today.iloc[0]
    started = r["started_ts"].tz_convert(_ET)
    if pd.isna(r["finished_ts"]):
        issues.append(f"runner 崩溃未收尾(run {r['run_id']},started {started:%H:%M ET})")
        return [f"- 本日心跳:`{r['run_id']}` {started:%H:%M ET} 开始,**未收尾(崩溃)**"]
    finished = r["finished_ts"].tz_convert(_ET)
    if r["kill_switch"]:
        issues.append("kill-switch 处于触发态(HALT)")
    if pd.notna(r["error"]) and r["error"]:
        issues.append(f"runner 退出码 3(停):{str(r['error'])[:120]}")
        code = "**3(停)**"
    else:
        code = "0/2 正常(ledger 内不可区分,2=部分缺价已 skip;精确值见 runner 日志)"
    ok = r["export_ok"]
    export_note = "未知" if pd.isna(ok) else ("ok" if ok else "**失败**")
    if pd.notna(ok) and not ok:
        issues.append("本轮 parquet 导出失败(本报告读的是旧快照)")
    return [
        f"- 本日心跳:`{r['run_id']}` {started:%H:%M}→{finished:%H:%M ET} · 退出码 {code} · 导出 {export_note}",
        f"- 本轮动作:信号 {int(r['signals_seen'])} · 下单 {int(r['orders_placed'])}",
    ]


def _positions_section(pos: pd.DataFrame, lines: list[str]) -> int:
    if pos.empty:
        lines.append("- 当前持仓:**0 只**(观察期早期,持仓积累中)")
        return 0
    latest = pos[pos["snapshot_date"] == pos["snapshot_date"].max()]
    held = latest[latest["qty"] > 0].copy()
    if held.empty:
        lines.append("- 当前持仓:**0 只**(观察期早期,持仓积累中)")
        return 0
    held["cost"] = held["avg_cost"].astype(float) * held["qty"].astype(float)
    held["pnl_pct"] = (held["close"].astype(float) / held["avg_cost"].astype(float) - 1) * 100
    total_pct = float(held["unrealized_pnl"].astype(float).sum()) / float(held["cost"].sum()) * 100
    lines.append(f"- 当前持仓:**{len(held)} 只**(快照日 {pd.Timestamp(held['snapshot_date'].max()).date()})")
    lines.append("")
    lines.append("| ticker | qty | 成本 | 收盘 | 浮动盈亏% |")
    lines.append("|---|---|---|---|---|")
    for _, p in held.sort_values("pnl_pct", ascending=False).iterrows():
        lines.append(
            f"| {p['ticker']} | {float(p['qty']):g} | {float(p['avg_cost']):.2f} "
            f"| {float(p['close']):.2f} | {p['pnl_pct']:+.2f}% |"
        )
    lines.append("")
    lines.append(f"- 总浮动盈亏:**{total_pct:+.2f}%**(未实现,相对持仓成本)")
    return len(held)


def build_report(d: date) -> str:
    issues: list[str] = []
    lines = [f"# 模拟盘日报 — {d}(ET)", "", f"> {HEADER_NOTE}", "", "## Runner"]

    if not lr.export_available():
        lines += [
            "- ledger 导出尚不存在——前向 runner 还未产出首份快照。",
            "",
            "## 结论",
            "观察期早期,持仓积累中;暂无可报数据。若已过首个交易日的 14:30 PT 仍无导出,查 launchd 与 runner 日志。",
        ]
        return "\n".join(lines) + "\n"

    runs = lr.load_agent_runs(10)
    lines += _runner_section(runs, d, issues)

    status, age_min, export_ts = lr.freshness()
    if status == "stale" and d.weekday() < 5:
        export_et = export_ts.astimezone(_ET)
        if export_et.date() < d:
            issues.append(f"导出陈旧:最近快照 {export_et:%m-%d %H:%M ET},非当日数据")

    lines += ["", "## 持仓(未实现盈亏)"]
    n_held = _positions_section(lr.load_positions_eod(days=5), lines)

    orders = lr.load_orders_current()
    today_orders = orders[orders["submitted_ts"].map(_et_date) == d] if not orders.empty else orders
    n_open = int((today_orders["side"] == "buy").sum()) if not today_orders.empty else 0
    n_close = int((today_orders["side"] == "sell").sum()) if not today_orders.empty else 0
    lines += ["", "## 当日交易", f"- 建仓 {n_open} 笔 · 平仓 {n_close} 笔"]

    recon = lr.load_recon_status()
    bad_recon = recon[recon["recon"] != "ok"] if not recon.empty else recon
    if not bad_recon.empty:
        issues.append(f"对账 mismatch {len(bad_recon)} 日(最近 {bad_recon['trade_date'].max()})——按 §7 应停新单排查")

    lines += ["", "## 异常"]
    lines += [f"- ⚠️ {i}" for i in issues] if issues else ["- 无"]

    lines += ["", "## 结论"]
    if issues:
        lines.append(f"⚠️ 有 {len(issues)} 项需要关注(见上);数据口径以 ledger 为准,处置前先看 runner 日志。")
    elif n_open + n_close == 0 and n_held == 0:
        lines.append("观察期早期,持仓积累中;runner 正常,无异常。")
    else:
        lines.append(f"runner 正常,当日建仓 {n_open}/平仓 {n_close};盈亏为未实现口径,观察期早期不下结论。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="模拟盘每日一屏汇报(markdown → logs/daily_report.md + stdout)")
    parser.add_argument("--date", default="", help="报告日 YYYY-MM-DD(默认今日 ET)")
    args = parser.parse_args(argv)
    d = date.fromisoformat(args.date) if args.date else datetime.now(UTC).astimezone(_ET).date()
    report = build_report(d)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("日报已写 {}", REPORT_PATH)
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
