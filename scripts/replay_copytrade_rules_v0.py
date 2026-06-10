"""离线规则回放 — COPYTRADE_RULES_SPEC_V0 子集 × stock-picker call_outcomes。

口径(Lead 2026-06-10 指定 + Strat spec §6):
- 信号:is_call=1 & direction='bullish' & handle ∈ 最新诚实榜 21d PROVEN(今日名单,point-in-time 局限见报告)
- 入场:T_entry = call_outcomes.entry_date(喊单日后首个交易日收盘,T+1 close),entry_close ≥ $3
- 剔除回填长尾:call_date→entry_date 交易日 gap ≥ 5 的行(spec 附录:正常 gap=1 占 ~89%)
- 规则:同日同 handle×ticker 去重 → 同日同票多 handle 择优合并(wilson_lo→conviction→confidence→handle)
        → 一票一仓 → 7×24h PROVEN bearish 冲突跳过 → 单 handle ≤ 5 并发
- 退出:纯持有 21 交易日(= call_outcomes 自身窗口)。止损/翻转退出无法建模(无价格路径),见报告局限。
- 输出:reports/replay_copytrade_v0/replay_results.json

只读 stock-picker 库;不写任何外部状态。复现:uv run python scripts/replay_copytrade_rules_v0.py
"""

from __future__ import annotations

import bisect
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger

SP_DIR = Path.home() / ".stock-picker-mcp"
EXPORTS_DIR = Path.home() / "stock-picker-mcp" / "exports"
OUT_DIR = Path(__file__).resolve().parent.parent / "reports" / "replay_copytrade_v0"

MIN_PRICE = 3.0
# 回填长尾剔除:call_date→entry_date 日历日 gap ≥ 5 天(spec §6/附录口径)。
# 不能用"call_outcomes 日期并集"当交易日历算交易日 gap:该日历起点 2024-06-03,
# 更早的喊单(价格序列缺失被上游贴到首个可得收盘)在日历外,bisect 会误判 gap=1。
# 本子集实测日历 gap 正常簇 1-4 天(周末/假日),回填行最小 15 天,阈值 5 无歧义带。
BACKFILL_GAP_CAL = 5
HANDLE_CAP = 5               # 单 handle 并发仓上限
CONFLICT_WINDOW = timedelta(days=7)
DECISION_UTC_HOUR = 19, 30   # 15:30 ET(EDT)≈ 19:30 UTC;EST 时为近似,报告已声明
CONVICTION_RANK = {"high": 3, "medium": 2, "low": 1, None: 0, "": 0}
Z = 1.959963984540054        # 95%


def wilson_lo(hits: int, n: int) -> float | None:
    if n == 0:
        return None
    p = hits / n
    denom = 1 + Z * Z / n
    centre = p + Z * Z / (2 * n)
    margin = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return (centre - margin) / denom


def latest_leaderboard() -> tuple[Path, dict[str, float]]:
    files = sorted(EXPORTS_DIR.glob("leaderboard_honest_*.csv"))
    if not files:
        raise FileNotFoundError(f"no leaderboard CSV under {EXPORTS_DIR}")
    path = files[-1]
    proven: dict[str, float] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            # CRLF:末列 status 带 \r,必须 strip(INTEGRATION_NOTES §1)
            if row["horizon"].strip() == "21d" and row["status"].strip() == "PROVEN":
                proven[row["handle"].strip()] = float(row["wilson_lo"])
    return path, proven


def parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError, TypeError):
        return None


def main() -> None:
    csv_path, proven = latest_leaderboard()
    logger.info("PROVEN 21d handles ({}): {}", len(proven), sorted(proven))

    db = sqlite3.connect(f"file:{SP_DIR / 'trackrecord.db'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    ph = ",".join("?" * len(proven))
    handles = list(proven)

    outcomes = {
        (r["tweet_id"], r["ticker"]): r
        for r in db.execute("SELECT * FROM call_outcomes WHERE horizon_days=21")
    }
    def cal_gap(d1: str, d2: str) -> int:
        """d1→d2 日历日数;解析失败按回填处理(返回大数)。"""
        try:
            return (date.fromisoformat(d2) - date.fromisoformat(d1)).days
        except ValueError:
            return 9999

    bulls = db.execute(
        f"SELECT tweet_id, handle, ticker, call_ts, call_date, conviction, confidence "
        f"FROM analyst_calls WHERE is_call=1 AND direction='bullish' AND handle IN ({ph})",
        handles,
    ).fetchall()
    bears_by_ticker: dict[str, list[datetime]] = defaultdict(list)
    for r in db.execute(
        f"SELECT ticker, call_ts FROM analyst_calls "
        f"WHERE is_call=1 AND direction='bearish' AND handle IN ({ph})",
        handles,
    ):
        ts = parse_ts(r["call_ts"])
        if ts:
            bears_by_ticker[r["ticker"]].append(ts)
    for v in bears_by_ticker.values():
        v.sort()

    skips: Counter[str] = Counter()
    candidates = []
    for r in bulls:
        out = outcomes.get((r["tweet_id"], r["ticker"]))
        if out is None or out["status"] != "evaluated":
            skips["unevaluated_pending_or_no_price"] += 1
            continue
        if not out["entry_date"] or out["abnormal_return"] is None:
            skips["null_metrics"] += 1
            continue
        gap = cal_gap(r["call_date"], out["entry_date"])
        if gap >= BACKFILL_GAP_CAL:
            skips["backfill_gap_excluded"] += 1
            continue
        if out["entry_close"] is None or out["entry_close"] < MIN_PRICE:
            skips["price_below_min"] += 1
            continue
        candidates.append((r, out))
    logger.info("候选(评估完成+剔长尾+价格过滤): {}", len(candidates))

    # 同日(T_entry)同 handle×ticker 去重:留 call_ts 最新
    dedup: dict[tuple, tuple] = {}
    for r, out in candidates:
        key = (out["entry_date"], r["handle"], r["ticker"])
        prev = dedup.get(key)
        if prev is None or (r["call_ts"] or "") > (prev[0]["call_ts"] or ""):
            if prev is not None:
                skips["dedup_same_handle_ticker_day"] += 1
            dedup[key] = (r, out)
        else:
            skips["dedup_same_handle_ticker_day"] += 1

    by_day: dict[str, list[tuple]] = defaultdict(list)
    for r, out in dedup.values():
        by_day[out["entry_date"]].append((r, out))

    def priority(item: tuple) -> tuple:
        r, _ = item
        return (
            -proven[r["handle"]],
            -CONVICTION_RANK.get(r["conviction"], 0),
            -(r["confidence"] or 0.0),
            r["handle"],
            r["ticker"],
        )

    open_pos: list[dict] = []      # {ticker, handle, exit_date}
    trades: list[dict] = []
    concurrency: list[tuple[str, int]] = []

    for day in sorted(by_day):
        # 平仓:exit_date < 今日的仓位释放(exit 当日不腾槽,次日生效 — spec §1 槽位会计)
        open_pos = [p for p in open_pos if p["exit_date"] >= day]
        # 同日同票择优合并
        best_per_ticker: dict[str, tuple] = {}
        for item in sorted(by_day[day], key=priority):
            t = item[0]["ticker"]
            if t in best_per_ticker:
                skips["merged_same_ticker_day"] += 1
            else:
                best_per_ticker[t] = item
        for item in sorted(best_per_ticker.values(), key=priority):
            r, out = item
            if any(p["ticker"] == r["ticker"] for p in open_pos):
                skips["one_position_per_ticker"] += 1
                continue
            decision_ts = datetime.fromisoformat(day).replace(
                hour=DECISION_UTC_HOUR[0], minute=DECISION_UTC_HOUR[1]
            )
            bl = bears_by_ticker.get(r["ticker"], [])
            i = bisect.bisect_right(bl, decision_ts)
            if i > 0 and bl[i - 1] >= decision_ts - CONFLICT_WINDOW:
                skips["bearish_conflict_7d"] += 1
                continue
            if sum(1 for p in open_pos if p["handle"] == r["handle"]) >= HANDLE_CAP:
                skips["handle_cap"] += 1
                continue
            open_pos.append({"ticker": r["ticker"], "handle": r["handle"], "exit_date": out["exit_date"]})
            trades.append({
                "tweet_id": r["tweet_id"], "handle": r["handle"], "ticker": r["ticker"],
                "call_ts": r["call_ts"], "call_date": r["call_date"],
                "entry_date": out["entry_date"], "entry_close": out["entry_close"],
                "exit_date": out["exit_date"], "exit_close": out["exit_close"],
                "fwd_return": out["fwd_return"], "benchmark_return": out["benchmark_return"],
                "abnormal_return": out["abnormal_return"], "is_hit": out["is_hit"],
            })
        concurrency.append((day, len(open_pos)))

    def pct(vals: list[float], q: float) -> float:
        s = sorted(vals)
        k = (len(s) - 1) * q
        lo, hi = math.floor(k), math.ceil(k)
        return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)

    def stats(rows: list[dict]) -> dict:
        ab = [t["abnormal_return"] for t in rows]
        fwd = [t["fwd_return"] for t in rows]
        graded = [t for t in rows if t["is_hit"] is not None]
        hits = sum(t["is_hit"] for t in graded)
        return {
            "n": len(rows),
            "graded_n": len(graded),
            "hits": hits,
            "hit_rate": round(hits / len(graded), 4) if graded else None,
            "wilson_lo_95": round(wilson_lo(hits, len(graded)), 4) if graded else None,
            "abnormal_mean": round(sum(ab) / len(ab), 5) if ab else None,
            "abnormal_median": round(pct(ab, 0.5), 5) if ab else None,
            "abnormal_p10": round(pct(ab, 0.1), 5) if ab else None,
            "abnormal_p90": round(pct(ab, 0.9), 5) if ab else None,
            "fwd_mean": round(sum(fwd) / len(fwd), 5) if fwd else None,
            "fwd_median": round(pct(fwd, 0.5), 5) if fwd else None,
            "fwd_win_rate": round(sum(1 for x in fwd if x > 0) / len(fwd), 4) if fwd else None,
            "worst_fwd": round(min(fwd), 5) if fwd else None,
            "best_fwd": round(max(fwd), 5) if fwd else None,
        }

    by_handle = {h: stats([t for t in trades if t["handle"] == h]) for h in sorted(proven)}
    by_year = {y: stats([t for t in trades if t["entry_date"][:4] == y])
               for y in sorted({t["entry_date"][:4] for t in trades})}
    conc_vals = [c for _, c in concurrency]
    over10 = sum(1 for c in conc_vals if c > 10)

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "leaderboard_csv": csv_path.name,
        "proven_handles": {h: proven[h] for h in sorted(proven)},
        "params": {"min_price": MIN_PRICE, "backfill_gap_cal_days": BACKFILL_GAP_CAL,
                   "handle_cap": HANDLE_CAP, "conflict_window_days": 7,
                   "exit": "hold_21_trading_days_only"},
        "raw_bullish_calls": len(bulls),
        "skips": dict(skips),
        "trades": stats(trades),
        "by_handle": by_handle,
        "by_year": by_year,
        "concurrency": {"max": max(conc_vals) if conc_vals else 0,
                        "mean": round(sum(conc_vals) / len(conc_vals), 2) if conc_vals else 0,
                        "decision_days": len(conc_vals),
                        "days_over_10_slots": over10},
        "trades_detail": trades,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "replay_results.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    logger.info("trades={} graded={} hit_rate={} wilson_lo={} abnormal_mean={}",
                result["trades"]["n"], result["trades"]["graded_n"],
                result["trades"]["hit_rate"], result["trades"]["wilson_lo_95"],
                result["trades"]["abnormal_mean"])
    logger.info("skips: {}", dict(skips))
    logger.info("written: {}", out_path)


if __name__ == "__main__":
    main()
