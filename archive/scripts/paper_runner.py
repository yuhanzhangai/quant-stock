"""v2.5A Rolling Paper Monitor — 8h cycles, persistent state, 3 sessions.

每 8 小时 finalize 一个 cycle 并自动启动下一轮。
Open positions / cooldown / risk state 跨 cycle 继承。

Usage:
    .venv/Scripts/python.exe scripts/paper_runner.py
    .venv/Scripts/python.exe scripts/paper_runner.py --max-cycles 3
    .venv/Scripts/python.exe scripts/paper_runner.py --cycle-hours 8
"""

import asyncio
import json
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import polars as pl
from loguru import logger

from src.exchange.ccxt_client import CCXTClient
from src.research.db import connect_research_db
from src.strategies.minswing_v3_final import MinSwingV3Strategy

# ── Constants ──

SCAN_INTERVAL = 300  # 5 min
HEARTBEAT_INTERVAL = 1800  # 30 min
CYCLE_HOURS = 8
MAX_FAILED_HB = 3
MAX_BAR_DELAY = 2
MIN_DISK_GB = 5
MIN_BARS = 200

BASE_DIR = Path("data/research/paper_observation")
STATE_PATH = Path("data/paper_persistent_state.json")
HEARTBEAT_DIR = Path("reports/v2_5A_top50_paper_observation/heartbeat")
DAILY_DIR = Path("reports/v2_5A_top50_paper_observation/daily")

SESSIONS = {
    "core": {
        "name": "Session A: Core Paper",
        "symbols": ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"],
        "full_tracking": True,
    },
    "candidate": {
        "name": "Session B: Candidate Paper",
        "symbols": ["CFX-USDT", "PENGU-USDT", "ENJ-USDT", "DYDX-USDT", "DOGE-USDT"],
        "full_tracking": True,
    },
    "broad": {
        "name": "Session C: Broad Watchlist",
        "symbols": [
            "MASK-USDT",
            "ONDO-USDT",
            "ENA-USDT",
            "PYTH-USDT",
            "OKB-USDT",
            "TRUMP-USDT",
            "HYPE-USDT",
            "ZEC-USDT",
            "ORDI-USDT",
            "AVAX-USDT",
            "STRK-USDT",
            "ADA-USDT",
            "UNI-USDT",
            "LIT-USDT",
            "LINK-USDT",
            "CHZ-USDT",
            "SPK-USDT",
            "XLM-USDT",
            "AAVE-USDT",
            "BCH-USDT",
            "WLD-USDT",
            "TON-USDT",
            "XPL-USDT",
            "BTC-USDT",
            "PI-USDT",
            "SUI-USDT",
            "APE-USDT",
            "FIL-USDT",
            "WIF-USDT",
            "PUMP-USDT",
            "XRP-USDT",
            "PEPE-USDT",
            "TRX-USDT",
            "DOT-USDT",
            "LTC-USDT",
            "IP-USDT",
            "BNB-USDT",
            "POL-USDT",
        ],
        "full_tracking": False,
    },
}


# ── Persistent State ──


def load_state() -> dict:
    """Load cross-cycle persistent state."""
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {
        "observation_id": f"obs_{uuid.uuid4().hex[:8]}",
        "cycle_number": 0,
        "positions": {},  # {symbol: {entry_price, entry_ts}}
        "consecutive_losses": {},  # {symbol: count}
        "last_signal_ts": {},  # {symbol: ts} for dedup
        "total_signals": 0,
        "total_trades": 0,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }


def save_state(state: dict) -> None:
    """Save persistent state."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(tz=UTC).isoformat()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── Cycle DB ──


def init_cycle_db(cycle_dir: Path) -> sqlite3.Connection:
    """Initialize per-cycle SQLite DB."""
    db_path = cycle_dir / "cycle.sqlite"
    conn = sqlite3.connect(str(db_path))
    for table_sql in [
        "CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session TEXT, symbol TEXT, side TEXT, price REAL, status TEXT, reject_reason TEXT)",
        "CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session TEXT, symbol TEXT, decision TEXT, reason TEXT, price REAL)",
        "CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session TEXT, symbol TEXT, fill_price REAL, slippage_bps REAL, fee_bps REAL)",
        "CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, symbol TEXT, entry_ts TEXT, exit_ts TEXT, entry_price REAL, exit_price REAL, pnl_pct REAL, exit_reason TEXT)",
        "CREATE TABLE IF NOT EXISTS rejected_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session TEXT, symbol TEXT, reason TEXT, price REAL)",
        "CREATE TABLE IF NOT EXISTS heartbeats (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, status TEXT, core_sig INTEGER, cand_sig INTEGER, broad_sig INTEGER, errors INTEGER, disk_gb REAL)",
    ]:
        conn.execute(table_sql)
    conn.commit()
    return conn


# ── Scanner ──


async def scan_all_sessions(
    client: CCXTClient,
    conn: sqlite3.Connection,
    state: dict,
    paused: bool,
) -> dict:
    """Scan all 3 sessions."""
    strat = MinSwingV3Strategy()
    now_ts = datetime.now(tz=UTC).isoformat()
    all_stats = {}

    for skey, scfg in SESSIONS.items():
        stats = {"signals": 0, "accepted": 0, "rejected": 0, "errors": 0, "ok": 0}
        full = scfg["full_tracking"]

        for symbol in scfg["symbols"]:
            ccxt_sym = symbol.replace("-", "/")
            try:
                candles = await client.fetch_ohlcv(ccxt_sym, "5m", limit=300)
                if not candles or len(candles) < MIN_BARS:
                    continue

                pdf = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                pdf["datetime"] = pd.to_datetime(pdf["ts"], unit="ms", utc=True)
                price = pdf.set_index("datetime")["close"]
                coin = symbol.replace("-USDT", "")

                entries, exits = strat.generate_signals(price, coin=coin)
                recent_entry = entries.iloc[-3:].any()
                recent_exit = exits.iloc[-3:].any()
                cp = float(price.iloc[-1])

                has_pos = symbol in state["positions"]

                # Exit check
                if full and recent_exit and has_pos:
                    pos = state["positions"][symbol]
                    pnl = (cp - pos["entry_price"]) / pos["entry_price"] * 100
                    reason = "stop_loss" if pnl < -1.5 else ("take_profit" if pnl > 7 else "signal_exit")
                    conn.execute(
                        "INSERT INTO trades (session, symbol, entry_ts, exit_ts, entry_price, exit_price, pnl_pct, exit_reason) VALUES (?,?,?,?,?,?,?,?)",
                        (skey, symbol, pos["entry_ts"], now_ts, pos["entry_price"], cp, round(pnl, 4), reason),
                    )
                    conn.execute(
                        "INSERT INTO fills (ts, session, symbol, fill_price, slippage_bps, fee_bps) VALUES (?,?,?,?,3.0,5.0)",
                        (now_ts, skey, symbol, cp),
                    )
                    del state["positions"][symbol]
                    state["total_trades"] += 1

                # Entry check
                if recent_entry and not has_pos:
                    # Dedup: don't signal same bar twice
                    last_sig = state["last_signal_ts"].get(symbol, "")
                    if now_ts[:16] == last_sig[:16]:
                        continue

                    stats["signals"] += 1
                    state["total_signals"] += 1
                    state["last_signal_ts"][symbol] = now_ts

                    if paused:
                        conn.execute(
                            "INSERT INTO signals (ts, session, symbol, side, price, status, reject_reason) VALUES (?,?,?,'long',?,'rejected','bar_delay')",
                            (now_ts, skey, symbol, cp),
                        )
                        conn.execute(
                            "INSERT INTO rejected_signals (ts, session, symbol, reason, price) VALUES (?,?,?,'bar_delay',?)",
                            (now_ts, skey, symbol, cp),
                        )
                        conn.execute(
                            "INSERT INTO decisions (ts, session, symbol, decision, reason, price) VALUES (?,?,?,'reject','bar_delay',?)",
                            (now_ts, skey, symbol, cp),
                        )
                        stats["rejected"] += 1
                    else:
                        conn.execute(
                            "INSERT INTO signals (ts, session, symbol, side, price, status, reject_reason) VALUES (?,?,?,'long',?,'accepted','')",
                            (now_ts, skey, symbol, cp),
                        )
                        conn.execute(
                            "INSERT INTO decisions (ts, session, symbol, decision, reason, price) VALUES (?,?,?,'accept','signal_triggered',?)",
                            (now_ts, skey, symbol, cp),
                        )
                        conn.execute(
                            "INSERT INTO fills (ts, session, symbol, fill_price, slippage_bps, fee_bps) VALUES (?,?,?,?,3.0,5.0)",
                            (now_ts, skey, symbol, cp),
                        )
                        if full:
                            state["positions"][symbol] = {"entry_price": cp, "entry_ts": now_ts}
                        stats["accepted"] += 1

                stats["ok"] += 1
            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 1:
                    logger.warning(f"  [{skey}] {symbol}: {str(e)[:50]}")

        conn.commit()
        all_stats[skey] = stats

    return all_stats


# ── Heartbeat ──


def write_heartbeat(conn: sqlite3.Connection, all_stats: dict) -> str:
    """Write heartbeat to DB + file."""
    ts = datetime.now(tz=UTC)
    disk_gb = round(shutil.disk_usage(".").free / (1024**3), 1)
    total_err = sum(s["errors"] for s in all_stats.values())

    status = "failed" if disk_gb < MIN_DISK_GB else ("warning" if total_err > 0 else "normal")

    conn.execute(
        "INSERT INTO heartbeats (ts, status, core_sig, cand_sig, broad_sig, errors, disk_gb) VALUES (?,?,?,?,?,?,?)",
        (
            ts.isoformat(),
            status,
            all_stats["core"]["signals"],
            all_stats["candidate"]["signals"],
            all_stats["broad"]["signals"],
            total_err,
            disk_gb,
        ),
    )
    conn.commit()

    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    path = HEARTBEAT_DIR / f"{ts.strftime('%Y%m%d_%H%M')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Heartbeat {ts.strftime('%Y-%m-%d %H:%M UTC')}\n\nStatus: **{status}** | disk={disk_gb}GB\n\n")
        for k, s in all_stats.items():
            f.write(
                f"- {k}: sig={s['signals']} acc={s['accepted']} rej={s['rejected']} err={s['errors']} ok={s['ok']}\n"
            )

    return status


# ── Cycle Finalization ──


def finalize_cycle(cycle_dir: Path, conn: sqlite3.Connection, cycle_id: str, state: dict) -> None:
    """Export cycle artifacts and write DB records."""
    # Export tables to parquet
    for table in ["signals", "decisions", "rejected_signals", "fills", "trades"]:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        if rows:
            cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
            pl.DataFrame([dict(zip(cols, r, strict=False)) for r in rows]).write_parquet(
                str(cycle_dir / f"{table}.parquet")
            )

    # Cycle summary
    sig_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    accepted = conn.execute("SELECT COUNT(*) FROM signals WHERE status='accepted'").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM signals WHERE status='rejected'").fetchone()[0]
    trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    hb_count = conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
    hb_failed = conn.execute("SELECT COUNT(*) FROM heartbeats WHERE status='failed'").fetchone()[0]

    summary = {
        "cycle_id": cycle_id,
        "observation_id": state["observation_id"],
        "signals": sig_count,
        "accepted": accepted,
        "rejected": rejected,
        "trades": trade_count,
        "open_positions": len(state["positions"]),
        "heartbeats": hb_count,
        "heartbeats_failed": hb_failed,
        "finalized_at": datetime.now(tz=UTC).isoformat(),
    }

    with open(cycle_dir / "cycle_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Cycle report
    with open(cycle_dir / "cycle_report.md", "w", encoding="utf-8") as f:
        f.write(f"# Cycle Report: {cycle_id}\n\n**No strategy conclusions. Observation only.**\n\n")
        f.write("| Metric | Value |\n|--------|-------|\n")
        for k, v in summary.items():
            f.write(f"| {k} | {v} |\n")

        # Per-session breakdown
        for skey in SESSIONS:
            s_sig = conn.execute("SELECT COUNT(*) FROM signals WHERE session=?", (skey,)).fetchone()[0]
            s_acc = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE session=? AND status='accepted'", (skey,)
            ).fetchone()[0]
            s_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE session=?", (skey,)).fetchone()[0]
            f.write(f"\n## {skey}\n| Signals | {s_sig} | Accepted | {s_acc} | Trades | {s_trades} |\n")

    # Write to research.duckdb
    try:
        rdb = connect_research_db(required=True)
        rdb.execute(
            "INSERT INTO paper_sessions (session_id, strategy_name, strategy_version, "
            "initial_equity, total_signals, accepted_trades, rejected_signals, status, notes, created_at) "
            "VALUES (?, 'minswing_v3', '1.1.0', 50, ?, ?, ?, 'completed', ?, current_timestamp)",
            [cycle_id, sig_count, accepted, rejected, f"observation={state['observation_id']}"],
        )
        rdb.close()
    except Exception as e:
        logger.error(f"research.duckdb write failed: {e}")
        raise

    logger.info(f"Cycle finalized: {cycle_id} | sig={sig_count} acc={accepted} trades={trade_count}")


# ── Main Runner ──


async def run_cycle(client: CCXTClient, state: dict, cycle_id: str, cycle_dir: Path) -> bool:
    """Run one 8-hour cycle. Returns False if must stop."""
    conn = init_cycle_db(cycle_dir)
    cycle_end = time.time() + CYCLE_HOURS * 3600
    last_hb = 0
    consec_fails = 0
    scan_count = 0
    cumulative = {k: {"signals": 0, "accepted": 0, "rejected": 0, "errors": 0, "ok": 0} for k in SESSIONS}

    while time.time() < cycle_end:
        t0 = time.time()

        # Stop: disk
        if shutil.disk_usage(".").free / (1024**3) < MIN_DISK_GB:
            logger.error("STOP: disk low")
            conn.close()
            return False

        # Stop: heartbeat failures
        if consec_fails >= MAX_FAILED_HB:
            logger.error("STOP: heartbeat failures")
            conn.close()
            return False

        # Bar delay
        paused = False
        try:
            c = await client.fetch_ohlcv("BTC/USDT", "5m", limit=1)
            if c:
                delay = (time.time() * 1000 - c[-1][0]) / 300_000
                paused = delay > MAX_BAR_DELAY
        except Exception:
            paused = True

        # Scan
        all_stats = await scan_all_sessions(client, conn, state, paused)
        scan_count += 1
        for k in SESSIONS:
            for m in cumulative[k]:
                cumulative[k][m] += all_stats[k].get(m, 0)

        total_sig = sum(s["signals"] for s in all_stats.values())
        total_err = sum(s["errors"] for s in all_stats.values())
        logger.info(
            f"[{cycle_id}] Scan #{scan_count} ({time.time() - t0:.1f}s) | "
            f"sig={total_sig} err={total_err} pos={len(state['positions'])}"
        )

        # Heartbeat
        if time.time() - last_hb >= HEARTBEAT_INTERVAL:
            status = write_heartbeat(conn, all_stats)
            if status == "failed":
                consec_fails += 1
            else:
                consec_fails = 0

            # DB write test
            try:
                conn.execute("INSERT INTO heartbeats (ts, status) VALUES ('_t','_t')")
                conn.execute("DELETE FROM heartbeats WHERE ts='_t'")
                conn.commit()
            except Exception:
                logger.error("STOP: DB write failed")
                conn.close()
                return False

            last_hb = time.time()

        # Save state periodically
        save_state(state)

        # Sleep
        sleep = max(0, SCAN_INTERVAL - (time.time() - t0))
        if sleep > 0:
            await asyncio.sleep(sleep)

    # Finalize
    finalize_cycle(cycle_dir, conn, cycle_id, state)
    conn.close()
    return True


async def main(max_cycles: int = 0, cycle_hours: int = 8):
    """Main runner — loops cycles until stopped."""
    global CYCLE_HOURS
    CYCLE_HOURS = cycle_hours

    state = load_state()
    logger.info("=" * 60)
    logger.info("v2.5A Rolling Paper Monitor")
    logger.info(f"observation_id: {state['observation_id']}")
    logger.info(f"Cycle: {cycle_hours}h | Max cycles: {max_cycles or 'unlimited'}")
    logger.info(f"Positions inherited: {len(state['positions'])}")
    logger.info("=" * 60)

    for _skey, scfg in SESSIONS.items():
        logger.info(f"  {scfg['name']}: {len(scfg['symbols'])} symbols")

    cycle_num = state["cycle_number"]

    async with CCXTClient() as client:
        # API check
        try:
            await client.fetch_ohlcv("BTC/USDT", "5m", limit=1)
            logger.info("API: OK")
        except Exception as e:
            logger.error(f"API FAIL: {e}")
            return

        while True:
            cycle_num += 1
            state["cycle_number"] = cycle_num
            cycle_id = f"{state['observation_id']}_cycle{cycle_num:03d}"
            cycle_dir = BASE_DIR / f"observation={state['observation_id']}" / f"cycle={cycle_id}"
            cycle_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"\n{'=' * 60}")
            logger.info(f"Starting cycle {cycle_num}: {cycle_id}")
            logger.info(f"Open positions: {list(state['positions'].keys())}")
            logger.info(f"{'=' * 60}")

            ok = await run_cycle(client, state, cycle_id, cycle_dir)
            save_state(state)

            if not ok:
                logger.error("Runner stopped due to error")
                break

            if max_cycles > 0 and cycle_num >= max_cycles:
                logger.info(f"Reached max cycles ({max_cycles})")
                break

            logger.info(f"Cycle {cycle_num} complete. Starting next in 10s...")
            await asyncio.sleep(10)

    # Daily summary
    _write_daily_summary(state)
    logger.info(f"Runner finished. observation_id={state['observation_id']} cycles={cycle_num}")


def _write_daily_summary(state: dict) -> None:
    """Write daily summary across all cycles."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%d")
    path = DAILY_DIR / f"{ts}_daily_summary.md"

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Daily Summary — {ts}\n\n**No strategy conclusions.**\n\n")
        f.write("| Metric | Value |\n|--------|-------|\n")
        f.write(f"| observation_id | {state['observation_id']} |\n")
        f.write(f"| cycles_completed | {state['cycle_number']} |\n")
        f.write(f"| total_signals | {state['total_signals']} |\n")
        f.write(f"| total_trades | {state['total_trades']} |\n")
        f.write(f"| open_positions | {len(state['positions'])} |\n")
        if state["positions"]:
            f.write(
                "\n## Open Positions\n\n| Symbol | Entry Price | Entry Time |\n|--------|------------|------------|\n"
            )
            for sym, pos in state["positions"].items():
                f.write(f"| {sym} | {pos['entry_price']} | {pos['entry_ts'][:19]} |\n")

    logger.info(f"Daily summary: {path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="v2.5A Rolling Paper Monitor")
    parser.add_argument("--max-cycles", type=int, default=0, help="Max cycles (0=unlimited)")
    parser.add_argument("--cycle-hours", type=int, default=8, help="Hours per cycle")
    args = parser.parse_args()
    asyncio.run(main(max_cycles=args.max_cycles, cycle_hours=args.cycle_hours))
