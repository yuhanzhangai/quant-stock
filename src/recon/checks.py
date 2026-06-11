"""A 组对账检查器:ledger 内部完整性九项不变量(RECON_DESIGN_V0 §2 A1–A9)。

每项检查独立成函数、独立 try——一项炸了(表/视图缺失等)记 HALT finding,其余照跑;
检查器对 ledger **只读**,结果不回写(recon_runs/findings 表归 %Exec writer,设计 §5/§6 职责切分)。
判级:HALT = 停新单证据;WARN = 记录观察;封闭集,扩项升版。

与设计稿的 v0 实现取态(均在对应函数 docstring 注明):
- A4 的 WARN→HALT:超 1 个 NYSE 交易日未推进 = WARN,超 2 个 = HALT(设计稿只给了触发阈值,
  升级阈值是实现拍的,验收时报 Lead 知悉)。
- A5 终态封锁豁免更正行(corrects_seq 非空)——设计稿早于 ORDER_LEDGER_SPEC r3,按 r3 §5.2 对齐。
- A3 将 r3 新增的 `expired` 终态与 cancelled 同等处理(有 fill 须有 partial 历史)。

用法:`uv run python -m src.recon.checks <ledger.duckdb>`(产物落 reports/recon/<date>/),
或 Exec EOD 循环内 `run_checks(con)` 拿 findings 自行落库。
"""

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import duckdb
import exchange_calendars as xcals
from loguru import logger

from src.signals.paths import assert_writable_path

REPORTS_ROOT = Path(__file__).resolve().parents[2] / "reports" / "recon"
_TERMINAL = frozenset({"filled", "cancelled", "rejected", "expired"})


@dataclass(frozen=True)
class Finding:
    check_id: str
    severity: str  # 'halt' | 'warn'
    message: str
    ticker: str | None = None
    order_id: str | None = None
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class ReconConfig:
    a4_warn_trading_days: int = 1   # 超过即 WARN(设计默认:1 个回采周期 = 1 交易日)
    a4_halt_trading_days: int = 2   # 超过即 HALT(v0 实现取态)
    a7_tolerance_min: float = 5.0   # fill_ts 时区解析容差(设计 §3)
    a9_max_age_hours: float = 2.0   # 水位新鲜度阈值 = 2× 轮询间隔(轮询 1h 假设,待 Exec 定后对齐)


@dataclass(frozen=True)
class ReconResult:
    result: str  # 'pass' | 'warn' | 'halt'
    checks_run: int
    checks_failed: int
    findings: list[Finding] = field(default_factory=list)


@lru_cache(maxsize=1)
def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS")


def _trading_days_since(ts: datetime, now: datetime) -> int:
    """ts 与 now 之间(不含 ts 当日)的 NYSE 交易日数。"""
    cal = _xnys()
    start, end = ts.astimezone(UTC).date(), now.astimezone(UTC).date()
    if end <= start:
        return 0
    return len(cal.sessions_in_range(start + timedelta(days=1), end))


def check_a1_orphan_fills(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A1(HALT):有效成交的 order_id 必须在 orders 存在——孤儿成交 = 漏记订单或回采串单。"""
    rows = con.execute(
        "SELECT f.fill_id, f.order_id FROM v_fills_effective f "
        "WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.order_id = f.order_id)").fetchall()
    return [Finding("A1", "halt", f"孤儿成交 {fill_id}:order_id 不存在于 orders", order_id=order_id,
                    expected="orders 中存在", actual="缺失") for fill_id, order_id in rows]


def check_a2_overfill(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A2(HALT):filled_qty ≤ 委托 qty——超额成交 = 重复回采或重复下单。"""
    rows = con.execute(
        "SELECT o.order_id, o.ticker, o.qty, vf.filled_qty FROM v_orders_current o "
        "JOIN v_order_filled vf USING (order_id) WHERE vf.filled_qty > o.qty").fetchall()
    return [Finding("A2", "halt", "超额成交", ticker=t, order_id=oid, expected=f"filled ≤ {q}", actual=str(fq))
            for oid, t, q, fq in rows]


def check_a3_status_fill_consistency(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A3(HALT):状态↔成交一致(filled⟺全成;partial⟺部分;rejected⟹无 fill;
    cancelled/expired 有 fill ⟹ 有 partial 历史)。"""
    rows = con.execute(
        "SELECT o.order_id, o.ticker, o.status, o.qty, coalesce(vf.filled_qty, 0) AS filled_qty "
        "FROM v_orders_current o LEFT JOIN v_order_filled vf USING (order_id)").fetchall()
    with_partial_hist = {r[0] for r in con.execute(
        "SELECT DISTINCT order_id FROM orders WHERE status = 'partial'").fetchall()}
    findings: list[Finding] = []
    for oid, ticker, status, qty, filled in rows:
        bad: str | None = None
        if status == "filled" and filled != qty:
            bad = f"filled 态但 filled_qty={filled} ≠ qty={qty}"
        elif status == "partial" and not (0 < filled < qty):
            bad = f"partial 态但 filled_qty={filled} 不在 (0, {qty}) 内"
        elif status == "rejected" and filled > 0:
            bad = f"rejected 态却有有效成交 {filled}"
        elif status in ("cancelled", "expired") and filled > 0 and oid not in with_partial_hist:
            bad = f"{status} 态有成交 {filled} 但事件流无 partial 历史"
        if bad:
            findings.append(Finding("A3", "halt", bad, ticker=ticker, order_id=oid))
    return findings


def check_a4_stale_submitted(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A4(WARN→HALT):submitted 出现 fill 但状态未推进;超 1 交易日 WARN、超 2 交易日 HALT(v0 取态)。"""
    rows = con.execute(
        "SELECT o.order_id, o.ticker, vf.first_fill_ts FROM v_orders_current o "
        "JOIN v_order_filled vf USING (order_id) WHERE o.status = 'submitted'").fetchall()
    findings: list[Finding] = []
    for oid, ticker, first_fill_ts in rows:
        days = _trading_days_since(first_fill_ts, now)
        if days > cfg.a4_halt_trading_days:
            sev = "halt"
        elif days > cfg.a4_warn_trading_days:
            sev = "warn"
        else:
            continue  # 回采周期内的正常时滞
        findings.append(Finding("A4", sev, f"有成交但状态停在 submitted 已 {days} 个交易日",
                                ticker=ticker, order_id=oid, actual=f"first_fill_ts={first_fill_ts}"))
    return findings


def check_a5_event_stream(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A5(HALT):seq 从 0 连续无缺口;终态后无后续行(唯一豁免:corrects_seq 非空的更正行,r3 §5.2)。"""
    rows = con.execute(
        "SELECT order_id, list(seq ORDER BY seq), list(status ORDER BY seq), list(corrects_seq ORDER BY seq) "
        "FROM orders GROUP BY order_id").fetchall()
    findings: list[Finding] = []
    for oid, seqs, statuses, corrects in rows:
        if list(seqs) != list(range(len(seqs))):
            findings.append(Finding("A5", "halt", f"事件流 seq 不连续: {seqs}", order_id=oid,
                                    expected=f"0..{len(seqs) - 1} 连续", actual=str(seqs)))
        terminal_at: int | None = None
        for seq, status, corr in zip(seqs, statuses, corrects, strict=True):
            if terminal_at is not None and corr is None:
                findings.append(Finding("A5", "halt",
                                        f"终态(seq={terminal_at})后出现非更正行 seq={seq}({status})", order_id=oid))
                break
            if terminal_at is None and status in _TERMINAL:
                terminal_at = seq
    return findings


def check_a6_relation_chain(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A6(HALT):signal_id 存在;buy ⟹ decision='followed';sell ⟹ exit_reason 非空,direction_flip ⟹ 有触发信号。"""
    findings = [Finding("A6", "halt", "订单引用不存在的 signal_id", order_id=oid, actual=sid)
                for oid, sid in con.execute(
                    "SELECT DISTINCT o.order_id, o.signal_id FROM orders o "
                    "WHERE NOT EXISTS (SELECT 1 FROM signals s WHERE s.signal_id = o.signal_id)").fetchall()]
    findings += [Finding("A6", "halt", "开仓单的信号 decision 非 followed", ticker=t, order_id=oid, actual=dec)
                 for oid, t, dec in con.execute(
                     "SELECT o.order_id, o.ticker, s.decision FROM v_orders_current o "
                     "JOIN signals s USING (signal_id) WHERE o.side = 'buy' AND s.decision <> 'followed'").fetchall()]
    findings += [Finding("A6", "halt", "平仓单 exit_reason 为空", ticker=t, order_id=oid)
                 for oid, t in con.execute(
                     "SELECT order_id, ticker FROM v_orders_current "
                     "WHERE side = 'sell' AND exit_reason IS NULL").fetchall()]
    findings += [Finding("A6", "halt", "direction_flip 平仓缺 exit_trigger_signal_id", ticker=t, order_id=oid)
                 for oid, t in con.execute(
                     "SELECT order_id, ticker FROM v_orders_current "
                     "WHERE exit_reason = 'direction_flip' AND exit_trigger_signal_id IS NULL").fetchall()]
    return findings


def check_a7_timeline(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A7(WARN):call_ts ≤ ingested_ts ≤ submitted_ts;fill_ts ≥ submitted_ts − 容差(页面时区解析易错)。"""
    findings = [Finding("A7", "warn", "信号 ingested_ts 早于 call_ts", actual=f"signal_id={sid}")
                for (sid,) in con.execute(
                    "SELECT signal_id FROM signals WHERE ingested_ts < call_ts").fetchall()]
    findings += [Finding("A7", "warn", "submitted_ts 早于信号 ingested_ts", order_id=oid)
                 for (oid,) in con.execute(
                     "SELECT DISTINCT o.order_id FROM orders o JOIN signals s USING (signal_id) "
                     "WHERE o.submitted_ts < s.ingested_ts").fetchall()]
    findings += [Finding("A7", "warn", "fill_ts 早于 submitted_ts 超容差", order_id=oid,
                         actual=f"fill_id={fid}", expected=f"≥ submitted_ts − {cfg.a7_tolerance_min}min")
                 for fid, oid in con.execute(
                     "SELECT f.fill_id, f.order_id FROM v_fills_effective f "
                     "JOIN v_orders_current o USING (order_id) "
                     f"WHERE f.fill_ts < o.submitted_ts - INTERVAL {int(cfg.a7_tolerance_min)} MINUTE").fetchall()]
    return findings


def check_a8_sell_within_holdings(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A8(HALT):按 fill_ts 逐票重放,任意时点累计净持仓 ≥ 0(v1 只做多,负持仓 = 记账错)。"""
    rows = con.execute(
        "SELECT ticker, fill_id, running FROM ("
        "  SELECT o.ticker, f.fill_id,"
        "         sum(CASE WHEN o.side = 'buy' THEN f.qty ELSE -f.qty END)"
        "           OVER (PARTITION BY o.ticker ORDER BY f.fill_ts, f.fill_id) AS running"
        "  FROM v_fills_effective f JOIN v_orders_current o USING (order_id)"
        ") WHERE running < 0").fetchall()
    return [Finding("A8", "halt", "重放出现负持仓(卖超持有)", ticker=t,
                    actual=f"fill {fid} 后净持仓 {run}") for t, fid, run in rows]


def check_a9_watermark_freshness(con: duckdb.DuckDBPyConnection, cfg: ReconConfig, now: datetime) -> list[Finding]:
    """A9(WARN):水位距今 ≤ 阈值——没有新信号 ≠ 系统健康,水位停了说明采集断了。"""
    row = con.execute("SELECT max(poll_ts) FROM ingest_watermark").fetchone()
    latest = row[0] if row else None
    if latest is None:
        return [Finding("A9", "warn", "ingest_watermark 为空:从未记录轮询水位")]
    age_h = (now - latest).total_seconds() / 3600
    if age_h > cfg.a9_max_age_hours:
        return [Finding("A9", "warn", f"水位陈旧 {age_h:.1f}h(阈值 {cfg.a9_max_age_hours}h)",
                        actual=str(latest))]
    return []


CHECKS = (check_a1_orphan_fills, check_a2_overfill, check_a3_status_fill_consistency, check_a4_stale_submitted,
          check_a5_event_stream, check_a6_relation_chain, check_a7_timeline, check_a8_sell_within_holdings,
          check_a9_watermark_freshness)


def run_checks(con: duckdb.DuckDBPyConnection, cfg: ReconConfig | None = None,
               now: datetime | None = None) -> ReconResult:
    """对已打开的 ledger 连接跑全部 A 组检查;单项异常(表/视图缺失等)记 HALT finding 不中断其余项。"""
    cfg = cfg or ReconConfig()
    now = now or datetime.now(UTC)
    findings: list[Finding] = []
    failed = 0
    for check in CHECKS:
        check_id = check.__name__.split("_")[1].upper()
        try:
            got = check(con, cfg, now)
        except duckdb.Error as exc:  # 缺表/缺视图 = ledger 不可审,本身就是验收发现
            got = [Finding(check_id, "halt", f"检查无法执行: {exc}")]
        if got:
            failed += 1
            findings.extend(got)
    if any(f.severity == "halt" for f in findings):
        result = "halt"
    elif findings:
        result = "warn"
    else:
        result = "pass"
    return ReconResult(result=result, checks_run=len(CHECKS), checks_failed=failed, findings=findings)


def run_recon(ledger_path: Path, cfg: ReconConfig | None = None, out_root: Path = REPORTS_ROOT) -> ReconResult:
    """只读打开 ledger 跑 A 组,产物(findings.json + RECON_RESULT.md)落 reports/recon/<日期>/。"""
    run_ts = datetime.now(UTC)
    con = duckdb.connect(str(ledger_path), read_only=True)
    try:
        result = run_checks(con, cfg, run_ts)
    finally:
        con.close()
    code_commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                 cwd=Path(__file__).parent, check=False).stdout.strip() or "unknown"
    out_dir = assert_writable_path(out_root / run_ts.strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"run_ts": run_ts.isoformat(timespec="seconds"), "ledger": str(ledger_path),
            "code_commit": code_commit, "scope": "ledger_only",  # A 组;B 组待页面能力(设计 §1.2)
            "result": result.result, "checks_run": result.checks_run, "checks_failed": result.checks_failed,
            "review_status": "未经独立复核(强制审核制度 2026-06-10 废止)",
            "findings": [asdict(f) for f in result.findings]}
    (out_dir / "findings.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# RECON — A 组 ledger 内部完整性(A1–A9)",
        "",
        f"> {meta['run_ts']} · ledger `{ledger_path}` · code_commit `{code_commit}` · scope ledger_only",
        "> **未经独立复核**(强制审核制度 2026-06-10 废止,按新质量纪律自检后发布)",
        "",
        f"**裁决:{result.result.upper()}**(9 项检查,{result.checks_failed} 项有发现,"
        f"{len(result.findings)} 条 finding)",
        "",
        "| check | severity | message | ticker | order_id |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {f.check_id} | {f.severity} | {f.message} | {f.ticker or '—'} | {f.order_id or '—'} |"
              for f in result.findings] or ["| — | — | 全部不变量成立 | — | — |"]
    (out_dir / "RECON_RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("recon 完成: {} / findings {} / 产物 {}", result.result, len(result.findings), out_dir)
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        logger.error("用法: uv run python -m src.recon.checks <ledger.duckdb>")
        sys.exit(2)
    res = run_recon(Path(sys.argv[1]))
    sys.exit({"pass": 0, "warn": 1}.get(res.result, 2))
