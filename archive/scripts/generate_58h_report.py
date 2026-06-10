"""Generate v2.5A 58-hour paper observation analysis package."""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3

base = Path("data/research/paper_observation/observation=obs_8477b07a")
state = json.load(open("data/paper_persistent_state.json"))
export_dir = Path("data/research/paper_sessions_export")
export_dir.mkdir(parents=True, exist_ok=True)
report_dir = Path("reports/v2_5A_top50_paper_observation")
report_dir.mkdir(parents=True, exist_ok=True)

# ── Collect ──
cycles, all_signals, all_trades, all_rejected, all_hb = [], [], [], [], []

for cd in sorted(base.glob("cycle=*")):
    sf = cd / "cycle_summary.json"
    if sf.exists():
        cycles.append(json.load(open(sf)))
    db = cd / "cycle.sqlite"
    if not db.exists():
        continue
    conn = sqlite3.connect(str(db))
    for r in conn.execute("SELECT ts,session,symbol,price,status,reject_reason FROM signals").fetchall():
        all_signals.append(dict(ts=r[0], session=r[1], symbol=r[2], price=r[3], status=r[4], reject_reason=r[5]))
    for r in conn.execute(
        "SELECT session,symbol,entry_ts,exit_ts,entry_price,exit_price,pnl_pct,exit_reason FROM trades"
    ).fetchall():
        all_trades.append(
            dict(
                session=r[0],
                symbol=r[1],
                entry_ts=r[2],
                exit_ts=r[3],
                entry_price=r[4],
                exit_price=r[5],
                pnl_pct=r[6],
                exit_reason=r[7],
            )
        )
    for r in conn.execute("SELECT ts,session,symbol,reason,price FROM rejected_signals").fetchall():
        all_rejected.append(dict(ts=r[0], session=r[1], symbol=r[2], reason=r[3], price=r[4]))
    for r in conn.execute(
        "SELECT ts,status,core_sig,cand_sig,broad_sig,errors,disk_gb FROM heartbeats WHERE ts NOT IN ('_test','_t')"
    ).fetchall():
        all_hb.append(dict(ts=r[0], status=r[1], errors=r[5]))
    conn.close()

obs_id = state["observation_id"]
first_ts = min(s["ts"] for s in all_signals) if all_signals else ""
last_ts = max(s["ts"] for s in all_signals) if all_signals else ""
hb_failed = sum(1 for h in all_hb if h["status"] == "failed")
hb_warning = sum(1 for h in all_hb if h["status"] == "warning")
total_errors = sum(h["errors"] for h in all_hb)
open_pos = state.get("positions", {})

# ── Session metrics ──
session_metrics = {}
for sess in ["core", "candidate", "broad"]:
    sigs = [s for s in all_signals if s["session"] == sess]
    closed = [t for t in all_trades if t["session"] == sess and t.get("exit_ts")]
    wins = [t for t in closed if t["pnl_pct"] > 0]
    total_pnl = sum(t["pnl_pct"] for t in closed)
    session_metrics[sess] = dict(
        signals=len(sigs),
        closed_trades=len(closed),
        wins=len(wins),
        losses=len(closed) - len(wins),
        win_rate=round(len(wins) / len(closed) * 100, 1) if closed else 0,
        total_pnl=round(total_pnl, 2),
        avg_pnl=round(total_pnl / len(closed), 3) if closed else 0,
    )

# ── Symbol metrics ──
sym_data = defaultdict(lambda: dict(session="", signals=0, closed=0, wins=0, losses=0, pnl=0))
for s in all_signals:
    sym_data[s["symbol"]]["session"] = s["session"]
    sym_data[s["symbol"]]["signals"] += 1
for t in all_trades:
    if t.get("exit_ts"):
        d = sym_data[t["symbol"]]
        d["closed"] += 1
        d["pnl"] += t["pnl_pct"]
        if t["pnl_pct"] > 0:
            d["wins"] += 1
        else:
            d["losses"] += 1

# ── Reject reasons ──
reject_reasons = defaultdict(int)
for r in all_rejected:
    reject_reasons[r["reason"]] += 1

# ── Recommendations ──
recs = {}
for sym, d in sym_data.items():
    if d["session"] == "core":
        recs[sym] = "core_review" if d["closed"] >= 3 and d["pnl"] < -3 else "core_keep"
    elif d["session"] == "candidate":
        if d["closed"] >= 2 and d["pnl"] > 0:
            recs[sym] = "candidate_continue"
        elif d["closed"] >= 2 and d["pnl"] < -3:
            recs[sym] = "watch_only"
        else:
            recs[sym] = "candidate_continue"
    else:
        recs[sym] = "remove" if d["signals"] >= 3 and d["pnl"] < -10 else "watch_only"

# ── Export CSVs ──
with open(export_dir / "v2_5A_58h_symbol_metrics.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "symbol",
            "session",
            "signals",
            "closed_trades",
            "wins",
            "losses",
            "win_rate",
            "total_pnl",
            "avg_pnl",
            "recommendation",
        ]
    )
    for sym in sorted(sym_data, key=lambda x: sym_data[x]["pnl"], reverse=True):
        d = sym_data[sym]
        wr = round(d["wins"] / d["closed"] * 100, 1) if d["closed"] > 0 else 0
        avg = round(d["pnl"] / d["closed"], 3) if d["closed"] > 0 else 0
        w.writerow(
            [
                sym,
                d["session"],
                d["signals"],
                d["closed"],
                d["wins"],
                d["losses"],
                wr,
                round(d["pnl"], 2),
                avg,
                recs.get(sym, ""),
            ]
        )

with open(export_dir / "v2_5A_58h_trades.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["session", "symbol", "entry_ts", "exit_ts", "entry_price", "exit_price", "pnl_pct", "exit_reason"])
    for t in all_trades:
        w.writerow(
            [
                t["session"],
                t["symbol"],
                t["entry_ts"],
                t["exit_ts"],
                t["entry_price"],
                t["exit_price"],
                t["pnl_pct"],
                t["exit_reason"],
            ]
        )

with open(export_dir / "v2_5A_58h_rejections.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ts", "session", "symbol", "reason", "price"])
    for r in all_rejected:
        w.writerow([r["ts"], r["session"], r["symbol"], r["reason"], r["price"]])

with open(export_dir / "v2_5A_58h_liquidity.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["symbol", "session", "signal_count", "min_price", "max_price", "price_range_pct", "note"])
    by_sym = defaultdict(list)
    for s in all_signals:
        by_sym[s["symbol"]].append(s["price"])
    for sym in sorted(by_sym):
        prices = by_sym[sym]
        mn, mx = min(prices), max(prices)
        rng = (mx - mn) / mn * 100 if mn > 0 else 0
        w.writerow(
            [sym, sym_data[sym]["session"], len(prices), round(mn, 6), round(mx, 6), round(rng, 2), "price_range_proxy"]
        )

with open(export_dir / "v2_5A_58h_open_trades.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["symbol", "entry_price", "entry_ts", "note"])
    for sym, pos in open_pos.items():
        w.writerow([sym, pos["entry_price"], pos["entry_ts"], "still_open"])

# ── JSON ──
summary_json = dict(
    observation_id=obs_id,
    start_ts=first_ts,
    end_ts=last_ts,
    runtime_hours=58,
    cycle_count=len(cycles),
    heartbeat_count=len(all_hb),
    heartbeat_failed=hb_failed,
    heartbeat_warning=hb_warning,
    total_errors=total_errors,
    total_signals=len(all_signals),
    total_closed_trades=len(all_trades),
    total_rejections=len(all_rejected),
    open_positions=len(open_pos),
    session_metrics=session_metrics,
    rejection_reasons=dict(reject_reasons),
    top_winners=sorted(
        [dict(symbol=t["symbol"], pnl=t["pnl_pct"], session=t["session"]) for t in all_trades if t["pnl_pct"] > 0],
        key=lambda x: -x["pnl"],
    ),
    top_losers=sorted(
        [dict(symbol=t["symbol"], pnl=t["pnl_pct"], session=t["session"]) for t in all_trades if t["pnl_pct"] <= 0],
        key=lambda x: x["pnl"],
    ),
    recommendations=recs,
    cycles=[dict(id=c["cycle_id"], signals=c["signals"], trades=c["trades"], open=c["open_positions"]) for c in cycles],
)
with open(report_dir / "V2_5A_58H_SUMMARY.json", "w") as f:
    json.dump(summary_json, f, indent=2)

# ── MD Report ──
L = []
L.append("# v2.5A Top47 Paper Observation — 58 Hour Report\n")
L.append("**No strategy conclusions. Observation only. No promotions.**\n")
L.append("## Overview\n")
L.append("| Metric | Value |")
L.append("|--------|-------|")
L.append(f"| observation_id | {obs_id} |")
L.append(f"| Start | {first_ts[:19]} |")
L.append(f"| End | {last_ts[:19]} |")
L.append("| Runtime | ~58 hours |")
L.append(f"| Cycles | {len(cycles)} |")
L.append(f"| Heartbeats | {len(all_hb)} (failed={hb_failed}, warning={hb_warning}) |")
L.append(f"| Errors | {total_errors} |")
L.append(f"| Signals | {len(all_signals)} |")
L.append(f"| Closed trades | {len(all_trades)} |")
L.append(f"| Rejections | {len(all_rejected)} |")
L.append(f"| Open positions | {len(open_pos)} |\n")

L.append("## Session-Level Metrics\n")
L.append("| Session | Signals | Closed | Wins | Losses | WR | PnL | Avg |")
L.append("|---------|---------|--------|------|--------|-----|-----|-----|")
for sess in ["core", "candidate", "broad"]:
    m = session_metrics[sess]
    L.append(
        f"| {sess} | {m['signals']} | {m['closed_trades']} | {m['wins']} | {m['losses']} | {m['win_rate']}% | {m['total_pnl']:+.2f}% | {m['avg_pnl']:+.3f}% |"
    )
L.append("")

L.append("## Cycle Summary\n")
L.append("| Cycle | Signals | Trades | Open |")
L.append("|-------|---------|--------|------|")
for c in cycles:
    cid = c["cycle_id"].split("_")[-1]
    L.append(f"| {cid} | {c['signals']} | {c['trades']} | {c['open_positions']} |")
L.append("")

L.append("## Closed Trades\n")
L.append("| Session | Symbol | Entry | Exit | PnL | Reason |")
L.append("|---------|--------|-------|------|-----|--------|")
for t in sorted(all_trades, key=lambda x: x["pnl_pct"], reverse=True):
    L.append(
        f"| {t['session']} | {t['symbol']} | {t['entry_price']} | {t['exit_price']} | {t['pnl_pct']:+.2f}% | {t['exit_reason']} |"
    )
L.append("")

L.append("## Top Winners\n")
for t in sorted([t for t in all_trades if t["pnl_pct"] > 0], key=lambda x: -x["pnl_pct"]):
    L.append(f"- **{t['symbol']}** ({t['session']}): {t['pnl_pct']:+.2f}%")
L.append("")

L.append("## Top Losers\n")
for t in sorted([t for t in all_trades if t["pnl_pct"] <= 0], key=lambda x: x["pnl_pct"])[:5]:
    L.append(f"- **{t['symbol']}** ({t['session']}): {t['pnl_pct']:+.2f}%")
L.append("")

L.append("## Open Positions\n")
if open_pos:
    L.append("| Symbol | Entry | Time |")
    L.append("|--------|-------|------|")
    for sym, pos in open_pos.items():
        L.append(f"| {sym} | {pos['entry_price']} | {pos['entry_ts'][:19]} |")
else:
    L.append("No open positions.")
L.append("")

L.append("## Rejection Breakdown\n")
if reject_reasons:
    for reason, cnt in reject_reasons.items():
        L.append(f"- {reason}: {cnt}")
else:
    L.append("No rejections.")
L.append("")

L.append("## Symbol Metrics (Closed Trades Only)\n")
L.append("| Symbol | Session | Sigs | Trades | W/L | PnL | Action |")
L.append("|--------|---------|------|--------|-----|-----|--------|")
for sym in sorted(sym_data, key=lambda x: sym_data[x]["pnl"], reverse=True):
    d = sym_data[sym]
    if d["closed"] > 0:
        L.append(
            f"| {sym} | {d['session']} | {d['signals']} | {d['closed']} | {d['wins']}/{d['losses']} | {d['pnl']:+.2f}% | {recs.get(sym, '')} |"
        )
L.append("")

L.append("## Signals-Only Symbols (No Closed Trades)\n")
for sym in sorted(sym_data):
    d = sym_data[sym]
    if d["signals"] > 0 and d["closed"] == 0:
        L.append(f"- {sym} ({d['session']}): {d['signals']} signals")
L.append("")

L.append("## Recommended Actions\n")
L.append("**Observations only — no promotions.**\n")
for action in ["core_keep", "core_review", "candidate_continue", "watch_only", "remove"]:
    syms = sorted([s for s, a in recs.items() if a == action])
    if syms:
        L.append(f"### {action} ({len(syms)})\n")
        for s in syms:
            d = sym_data[s]
            L.append(f"- {s}: {d['signals']} sig, {d['closed']} trades, PnL={d['pnl']:+.2f}%")
        L.append("")

L.append("## Artifacts\n")
L.append("| File | Path |")
L.append("|------|------|")
L.append("| Summary MD | reports/v2_5A_top50_paper_observation/V2_5A_58H_SUMMARY.md |")
L.append("| Summary JSON | reports/v2_5A_top50_paper_observation/V2_5A_58H_SUMMARY.json |")
L.append("| Symbol metrics | data/research/paper_sessions_export/v2_5A_58h_symbol_metrics.csv |")
L.append("| Trades | data/research/paper_sessions_export/v2_5A_58h_trades.csv |")
L.append("| Rejections | data/research/paper_sessions_export/v2_5A_58h_rejections.csv |")
L.append("| Liquidity | data/research/paper_sessions_export/v2_5A_58h_liquidity.csv |")
L.append("| Open trades | data/research/paper_sessions_export/v2_5A_58h_open_trades.csv |")

with open(report_dir / "V2_5A_58H_SUMMARY.md", "w", encoding="utf-8") as f:
    f.write("\n".join(L))

# Print
print("Done.")
for p in [
    report_dir / "V2_5A_58H_SUMMARY.md",
    report_dir / "V2_5A_58H_SUMMARY.json",
    export_dir / "v2_5A_58h_symbol_metrics.csv",
    export_dir / "v2_5A_58h_trades.csv",
    export_dir / "v2_5A_58h_rejections.csv",
    export_dir / "v2_5A_58h_liquidity.csv",
    export_dir / "v2_5A_58h_open_trades.csv",
]:
    print(f"  [{'OK' if p.exists() else 'MISSING'}] {p} ({p.stat().st_size if p.exists() else 0} bytes)")
