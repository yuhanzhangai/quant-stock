"""E 账批任务:本地 PaperBroker 成交(ledger)→ 实际执行绩效 + S−E 归因(FOLLOW_PERF_SPEC §2/§2.3/§3)。

P2 口径修正(ROADMAP 2026-06-11):Firstrade 无模拟盘,**本地模拟成交 = E 账数据源**。
- 一笔跟单 = signal_id 配对的 buy(入场)/ sell(出场)腿;入场日 = 首笔 fill 的美东交易日(spec §2.1)。
- actual_return = exit_avg_fill / entry_avg_fill − 1(paper 零费用);基准 = SPY 同窗收盘对收盘
  (价源 price_cache,与上游 call_outcomes 同源,spec §6 降低基准漂移)。
- 未平仓单列 mark-to-market(price_cache 最新收盘,as-of 随价源新鲜度,不与已平仓混算)。
- S−E 归因(spec §2.3 四分量):entry_diff_bps / window_diff / early_exit_diff / cost(=0);
  恒等式不强求闭合(收盘/模拟成交混锚),报告给出解释覆盖率。
- 延迟:wall_latency = orders.call_to_submit_ms,按 spec §3.2 桶;actionable_latency 待 v1。

只读 ledger + trackrecord + prices;产物 reports/follow_perf/<run_date>/(与 S 账同期目录)。
用法:`uv run python -m src.perf.e_account <ledger.duckdb>`
"""

import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import exchange_calendars as xcals
import polars as pl
from loguru import logger

from src.perf.s_account import HORIZON_DAYS, lag_bucket
from src.signals.paths import STOCK_PICKER_HOME, TRACKRECORD_DB, assert_writable_path, connect_readonly

PRICES_DB = STOCK_PICKER_HOME / "prices.db"
REPORTS_ROOT = Path(__file__).resolve().parents[2] / "reports" / "follow_perf"
_ET = ZoneInfo("America/New_York")

_LEG_SQL = """
SELECT s.signal_id, s.tweet_id, s.ticker, s.handle, s.call_ts, s.ingested_ts,
       o.order_id, o.side, o.exit_reason, o.submitted_ts, o.call_to_submit_ms,
       f.filled_qty, f.avg_fill_price, f.first_fill_ts, f.last_fill_ts
FROM v_orders_current o
JOIN signals s USING (signal_id)
JOIN v_order_filled f USING (order_id)
WHERE o.side = ?
ORDER BY f.first_fill_ts
"""


def _et_date(ts: datetime) -> date:
    return ts.astimezone(_ET).date()


def load_prices(tickers: list[str], prices_db: Path = PRICES_DB) -> dict[tuple[str, str], float]:
    """price_cache 收盘价 {(ticker, 'YYYY-MM-DD'): close},只读。"""
    con = connect_readonly(prices_db)
    try:
        out: dict[tuple[str, str], float] = {}
        for i in range(0, len(tickers), 500):
            chunk = tickers[i:i + 500]
            ph = ", ".join("?" * len(chunk))
            out.update({(t, d): c for t, d, c in con.execute(
                f"SELECT ticker, date, close FROM price_cache WHERE ticker IN ({ph})", chunk)})
    finally:
        con.close()
    return out


def hold21_session(entry: date) -> date:
    """entry 之后第 21 个 NYSE 交易日(上游 exit 口径:序列索引 entry+21)。"""
    cal = xcals.get_calendar("XNYS")
    after = list(cal.sessions_in_range(entry + timedelta(days=1), entry + timedelta(days=60)))
    if len(after) < HORIZON_DAYS:
        raise ValueError(f"日历窗口不足 21 交易日: entry={entry}")
    return after[HORIZON_DAYS - 1].date()


def load_trades(ledger_path: Path) -> pl.DataFrame:
    """ledger 配对 buy/sell 腿 → 逐笔跟单(closed=有出场腿)。"""
    con = duckdb.connect(str(ledger_path), read_only=True)
    try:
        buys = con.execute(_LEG_SQL, ["buy"]).pl()
        sells = con.execute(_LEG_SQL, ["sell"]).pl()
    finally:
        con.close()
    if buys.is_empty():
        return buys
    sells = sells.group_by("signal_id").agg(
        pl.col("avg_fill_price").first().alias("exit_avg_fill"),
        pl.col("filled_qty").first().alias("exit_qty"),
        pl.col("first_fill_ts").first().alias("exit_fill_ts"),
        pl.col("exit_reason").first(),
        pl.len().alias("n_exit_orders"),
    )
    return (buys.rename({"avg_fill_price": "entry_avg_fill", "filled_qty": "entry_qty",
                         "first_fill_ts": "entry_fill_ts"})
            .drop("exit_reason")
            .join(sells, on="signal_id", how="left")
            .with_columns(
                # DECIMAL → Float64:后续与 float 价格混算,Decimal 与 float 运算在 Python 侧会 TypeError
                pl.col("entry_avg_fill", "exit_avg_fill", "entry_qty", "exit_qty").cast(pl.Float64),
                pl.col("entry_fill_ts").map_elements(_et_date, return_dtype=pl.Date).alias("entry_date"),
                pl.col("exit_fill_ts").map_elements(_et_date, return_dtype=pl.Date).alias("exit_date"),
                (pl.col("call_to_submit_ms") / 1000).alias("wall_latency_s"),
            )
            .with_columns(pl.col("wall_latency_s").map_elements(lag_bucket, return_dtype=pl.String)
                          .alias("wall_bucket")))


def _px(prices: dict[tuple[str, str], float], ticker: str, d: date | None) -> float | None:
    return prices.get((ticker, d.isoformat())) if d is not None else None


def _ret(prices: dict[tuple[str, str], float], ticker: str, d0: date | None, d1: date | None) -> float | None:
    p0, p1 = _px(prices, ticker, d0), _px(prices, ticker, d1)
    return (p1 / p0 - 1) if (p0 and p1) else None


def load_s_rows(tweet_tickers: list[tuple[str, str]],
                trackrecord_db: Path = TRACKRECORD_DB) -> dict[tuple[str, str], dict]:
    """call_outcomes 21d evaluated 行(S 账侧),键 (tweet_id, ticker)。"""
    con = connect_readonly(trackrecord_db)
    try:
        out: dict[tuple[str, str], dict] = {}
        for tid, tk in tweet_tickers:
            row = con.execute(
                "SELECT entry_date, entry_close, exit_date, exit_close, abnormal_return, status "
                "FROM call_outcomes WHERE tweet_id = ? AND ticker = ? AND horizon_days = ?",
                (tid, tk, HORIZON_DAYS)).fetchone()
            if row and row[5] == "evaluated":
                out[(tid, tk)] = {"s_entry_date": date.fromisoformat(row[0]), "s_entry_close": row[1],
                                  "s_exit_date": date.fromisoformat(row[2]), "s_exit_close": row[3],
                                  "s_abnormal": row[4]}
    finally:
        con.close()
    return out


def enrich(trades: pl.DataFrame, prices: dict[tuple[str, str], float],
           s_rows: dict[tuple[str, str], dict]) -> pl.DataFrame:
    """逐笔补 E 账收益/基准/MTM + S−E 四分量归因(spec §2.3)。纯函数,便于合成数据测试。"""
    rows = []
    for t in trades.iter_rows(named=True):
        ticker, entry_d, exit_d = t["ticker"], t["entry_date"], t["exit_date"]
        closed = exit_d is not None
        actual_return = (t["exit_avg_fill"] / t["entry_avg_fill"] - 1) if closed else None
        spy_ret = _ret(prices, "SPY", entry_d, exit_d) if closed else None
        actual_abnormal = (actual_return - spy_ret) if (actual_return is not None and spy_ret is not None) else None
        last = max((d for (tk, d) in prices if tk == ticker), default=None) if not closed else None
        unrealized = (_px(prices, ticker, date.fromisoformat(last)) / t["entry_avg_fill"] - 1) \
            if (not closed and last) else None
        s = s_rows.get((t["tweet_id"], ticker))
        entry_diff_bps = window_diff = early_exit_diff = se_gap = None
        if closed and s:
            entry_diff_bps = (t["entry_avg_fill"] / s["s_entry_close"] - 1) * 1e4
            e_win = _ret(prices, ticker, entry_d, exit_d)
            s_win = _ret(prices, ticker, s["s_entry_date"], s["s_exit_date"])
            window_diff = (e_win - s_win) if (e_win is not None and s_win is not None) else None
            if t["exit_reason"] == "hold_21d":
                early_exit_diff = 0.0
            else:
                h21 = hold21_session(entry_d)
                px_exit, px_h21, px_entry = (_px(prices, ticker, exit_d), _px(prices, ticker, h21),
                                             _px(prices, ticker, entry_d))
                early_exit_diff = ((px_exit - px_h21) / px_entry) \
                    if (px_exit and px_h21 and px_entry) else None
            if actual_abnormal is not None:
                se_gap = s["s_abnormal"] - actual_abnormal
        rows.append(t | {"closed": closed, "actual_return": actual_return, "spy_return": spy_ret,
                         "actual_abnormal": actual_abnormal, "unrealized_return": unrealized,
                         "mtm_asof": last, "s_abnormal": s["s_abnormal"] if s else None,
                         "entry_diff_bps": entry_diff_bps, "window_diff": window_diff,
                         "early_exit_diff": early_exit_diff, "cost": 0.0 if closed else None,
                         "se_gap": se_gap})
    return pl.DataFrame(rows)


def _fmt(v: object, pct: bool = True) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:+.2%}" if pct else f"{v:+.0f}"


def render_report(df: pl.DataFrame, meta: dict[str, object]) -> str:
    closed = df.filter(pl.col("closed"))
    opened = df.filter(~pl.col("closed"))
    lines = [
        "# FOLLOW_PERF — E 账快照(execution / 本地 PaperBroker 成交)",
        "",
        f"> 生成 {meta['run_ts']} · ledger `{meta['ledger']}` · code_commit `{meta['code_commit']}` "
        f"· 复现:`{meta['command']}`",
        "> **未经独立复核**(强制审核制度 2026-06-10 废止,按新质量纪律自检后发布)",
        "",
        f"## 概览:{df.height} 笔跟单 = 已平仓 {closed.height} + 未平仓 {opened.height}(两类不混算)",
        "",
    ]
    if closed.height:
        agg = closed.select(pl.col("actual_return").mean().alias("r_mean"),
                            pl.col("actual_return").median().alias("r_med"),
                            pl.col("actual_abnormal").mean().alias("a_mean"),
                            pl.col("actual_abnormal").median().alias("a_med")).row(0, named=True)
        lines += [
            "## 已平仓(E 账)",
            "",
            "| n | actual_return mean/median | actual_abnormal(对 SPY)mean/median |",
            "|---|---|---|",
            f"| {closed.height} | {_fmt(agg['r_mean'])} / {_fmt(agg['r_med'])} "
            f"| {_fmt(agg['a_mean'])} / {_fmt(agg['a_med'])} |",
            "",
            "### 按 exit_reason(自建退出逻辑的贡献必须可见,spec §2.2)",
            "",
            "| exit_reason | n | actual_return mean | actual_abnormal mean |",
            "|---|---|---|---|",
        ]
        for (reason,), g in closed.group_by(["exit_reason"], maintain_order=True):
            lines.append(f"| {reason} | {g.height} | {_fmt(g['actual_return'].mean())} "
                         f"| {_fmt(g['actual_abnormal'].mean())} |")
        attr = closed.filter(pl.col("s_abnormal").is_not_null())
        lines += [
            "",
            f"### S−E 归因(spec §2.3;S 账 evaluated 覆盖 {attr.height}/{closed.height} 笔,"
            "未熟/无价不归因)",
            "",
            "| signal | ticker | exit_reason | S abn | E abn | S−E | entry_diff_bps | window_diff "
            "| early_exit_diff |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in attr.iter_rows(named=True):
            lines.append(f"| {r['signal_id'][:20]}… | {r['ticker']} | {r['exit_reason']} "
                         f"| {_fmt(r['s_abnormal'])} | {_fmt(r['actual_abnormal'])} | {_fmt(r['se_gap'])} "
                         f"| {_fmt(r['entry_diff_bps'], pct=False)} | {_fmt(r['window_diff'])} "
                         f"| {_fmt(r['early_exit_diff'])} |")
        lines += [
            "",
            "## wall_latency 分桶(call_ts→submitted_ts,spec §3.2;actionable_latency 待 v1)",
            "",
            "| 桶 | n | actual_abnormal mean |",
            "|---|---|---|",
        ]
        for (b,), g in closed.group_by(["wall_bucket"], maintain_order=True):
            lines.append(f"| {b} | {g.height} | {_fmt(g['actual_abnormal'].mean())} |")
    if opened.height:
        lines += ["", "## 未平仓(mark-to-market,单列)", "",
                  "| ticker | entry_date | entry_avg_fill | unrealized | as-of |", "|---|---|---|---|---|"]
        lines += [f"| {r['ticker']} | {r['entry_date']} | {r['entry_avg_fill']} "
                  f"| {_fmt(r['unrealized_return'])} | {r['mtm_asof'] or '—'} |"
                  for r in opened.iter_rows(named=True)]
    lines += [
        "",
        "## 已知局限",
        "",
        "- E 账成交价来自**本地 PaperBroker 模拟**(真实市场价+配置滑点),非券商真实成交;"
        "P3 真钱后两套 E 账并行对比。",
        "- 模拟成交价 vs SPY 收盘锚的日内基准误差,v0 接受并文档化(spec §2.1)。",
        "- 归因恒等式不强求闭合(混锚);S−E gap 的解释覆盖率见归因表。",
        "- mark-to-market 用 price_cache 最新收盘,as-of 随上游价源新鲜度。",
        "- 绩效报告是观察记录,不是策略验证(spec §5.4)。",
    ]
    return "\n".join(lines) + "\n"


def run(ledger_path: Path, out_root: Path = REPORTS_ROOT, prices_db: Path = PRICES_DB,
        trackrecord_db: Path = TRACKRECORD_DB) -> Path:
    run_ts = datetime.now(UTC)
    trades = load_trades(ledger_path)
    if trades.is_empty():
        raise RuntimeError(f"ledger 无成交 buy 腿,E 账无米下锅: {ledger_path}")
    tickers = sorted({*trades.get_column("ticker").to_list(), "SPY"})
    prices = load_prices(tickers, prices_db)
    s_rows = load_s_rows(list({(t["tweet_id"], t["ticker"]) for t in trades.iter_rows(named=True)}), trackrecord_db)
    df = enrich(trades, prices, s_rows)
    code_commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                 cwd=Path(__file__).parent, check=False).stdout.strip() or "unknown"
    meta: dict[str, object] = {
        "run_ts": run_ts.isoformat(timespec="seconds"), "code_commit": code_commit,
        "command": f"uv run python -m src.perf.e_account {ledger_path}",
        "ledger": str(ledger_path), "prices_db": str(prices_db), "trackrecord_db": str(trackrecord_db),
        "trades": df.height, "closed": int(df.get_column("closed").sum()),
        "attributed": int(df.get_column("se_gap").is_not_null().sum()),
        "review_status": "未经独立复核(强制审核制度 2026-06-10 废止)",
    }
    out_dir = assert_writable_path(out_root / run_ts.strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_dir / "e_account.parquet")
    (out_dir / "E_ACCOUNT_REPORT.md").write_text(render_report(df, meta), encoding="utf-8")
    (out_dir / "e_run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("E 账快照完成: {} 笔(closed {} / attributed {})/ 产物 {}",
                df.height, meta["closed"], meta["attributed"], out_dir)
    return out_dir


if __name__ == "__main__":
    if len(sys.argv) != 2:
        logger.error("用法: uv run python -m src.perf.e_account <ledger.duckdb>")
        sys.exit(2)
    run(Path(sys.argv[1]))
