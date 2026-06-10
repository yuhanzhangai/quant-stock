"""v2.5A 分层 Paper Monitor — 3 独立 Session。

Session A: Core Paper (ETH, SOL, NEAR, ARB) — full position tracking
Session B: Candidate Paper (CFX, PENGU, ENJ, DYDX, DOGE) — full tracking, no promotion
Session C: Broad Watchlist (其余 Top47) — signals + decisions only

Usage:
    .venv/Scripts/python.exe scripts/paper_monitor_v2.py
    .venv/Scripts/python.exe scripts/paper_monitor_v2.py --duration 480
"""

import asyncio
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from src.exchange.ccxt_client import CCXTClient
from src.strategies.minswing_v3_final import MinSwingV3Strategy

# ── Session Definitions ──

SESSIONS = {
    "core": {
        "name": "Session A: Core Paper",
        "symbols": ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"],
        "full_tracking": True,
        "db_path": Path("data/paper_session_core.sqlite"),
        "heartbeat_dir": Path("reports/v2_5A_top50_paper_observation/heartbeat/core"),
        "output_dir": Path("data/research/paper_sessions/v2_5A_core"),
    },
    "candidate": {
        "name": "Session B: Candidate Paper",
        "symbols": ["CFX-USDT", "PENGU-USDT", "ENJ-USDT", "DYDX-USDT", "DOGE-USDT"],
        "full_tracking": True,
        "db_path": Path("data/paper_session_candidate.sqlite"),
        "heartbeat_dir": Path("reports/v2_5A_top50_paper_observation/heartbeat/candidate"),
        "output_dir": Path("data/research/paper_sessions/v2_5A_candidate"),
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
        "db_path": Path("data/paper_session_broad.sqlite"),
        "heartbeat_dir": Path("reports/v2_5A_top50_paper_observation/heartbeat/broad"),
        "output_dir": Path("data/research/paper_sessions/v2_5A_broad"),
    },
}

SCAN_INTERVAL = 300
HEARTBEAT_INTERVAL = 1800
MAX_FAILED_HEARTBEATS = 3
MAX_BAR_DELAY_BARS = 2
MIN_DISK_FREE_GB = 5
MIN_BARS = 200


# ── DB ──


def init_db(db_path: Path, full_tracking: bool) -> sqlite3.Connection:
    """Initialize session DB."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, side TEXT, price REAL,
            status TEXT, reject_reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    if full_tracking:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL, entry_ts TEXT, entry_bar_idx INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, entry_ts TEXT, exit_ts TEXT,
                entry_price REAL, exit_price REAL,
                pnl_pct REAL, exit_reason TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, status TEXT, symbols_scanned INTEGER,
            signal_count INTEGER, accepted_count INTEGER,
            rejected_count INTEGER, error_count INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


# ── Scanner ──


async def scan_session(
    client: CCXTClient,
    session_key: str,
    session_cfg: dict,
    conn: sqlite3.Connection,
    bar_delay_paused: bool,
) -> dict:
    """Scan one session's symbols."""
    strat = MinSwingV3Strategy()
    stats = {"signals": 0, "accepted": 0, "rejected": 0, "errors": 0, "ok": 0}
    now_ts = datetime.now(tz=UTC).isoformat()
    full = session_cfg["full_tracking"]

    for symbol in session_cfg["symbols"]:
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

            # Check recent signals
            recent_entry = entries.iloc[-3:].any()
            recent_exit = exits.iloc[-3:].any()
            current_price = float(price.iloc[-1])

            if full:
                # Position tracking
                has_pos = conn.execute("SELECT 1 FROM positions WHERE symbol=?", (symbol,)).fetchone()

                if recent_exit and has_pos:
                    pos = conn.execute(
                        "SELECT entry_price, entry_ts FROM positions WHERE symbol=?", (symbol,)
                    ).fetchone()
                    pnl = (current_price - pos[0]) / pos[0] * 100
                    reason = "stop_loss" if pnl < -1.5 else ("take_profit" if pnl > 7 else "signal_exit")
                    conn.execute(
                        "INSERT INTO trades (symbol, entry_ts, exit_ts, entry_price, exit_price, pnl_pct, exit_reason) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (symbol, pos[1], now_ts, pos[0], current_price, round(pnl, 4), reason),
                    )
                    conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))

                if recent_entry and not has_pos:
                    stats["signals"] += 1
                    if bar_delay_paused:
                        conn.execute(
                            "INSERT INTO signals (ts, symbol, side, price, status, reject_reason) VALUES (?,?,'long',?,'rejected','bar_delay')",
                            (now_ts, symbol, current_price),
                        )
                        stats["rejected"] += 1
                    else:
                        conn.execute(
                            "INSERT INTO signals (ts, symbol, side, price, status, reject_reason) VALUES (?,?,'long',?,'accepted','')",
                            (now_ts, symbol, current_price),
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO positions (symbol, entry_price, entry_ts) VALUES (?,?,?)",
                            (symbol, current_price, now_ts),
                        )
                        stats["accepted"] += 1
            else:
                # Broad: signals + decisions only
                if recent_entry:
                    stats["signals"] += 1
                    status = "rejected" if bar_delay_paused else "accepted"
                    reason = "bar_delay" if bar_delay_paused else ""
                    conn.execute(
                        "INSERT INTO signals (ts, symbol, side, price, status, reject_reason) VALUES (?,?,'long',?,?,?)",
                        (now_ts, symbol, current_price, status, reason),
                    )
                    if bar_delay_paused:
                        stats["rejected"] += 1
                    else:
                        stats["accepted"] += 1

            stats["ok"] += 1
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 2:
                logger.warning(f"  [{session_key}] {symbol}: {str(e)[:50]}")

    conn.commit()
    return stats


# ── Heartbeat ──


def save_heartbeat(session_key: str, cfg: dict, conn: sqlite3.Connection, stats: dict) -> str:
    """Save heartbeat for one session."""
    disk = shutil.disk_usage(".")
    disk_free = round(disk.free / (1024**3), 1)
    ts = datetime.now(tz=UTC)

    if disk_free < MIN_DISK_FREE_GB or stats["errors"] > len(cfg["symbols"]) // 2:
        status = "failed"
    elif stats["errors"] > 0:
        status = "warning"
    else:
        status = "normal"

    conn.execute(
        "INSERT INTO heartbeats (ts, status, symbols_scanned, signal_count, accepted_count, rejected_count, error_count) "
        "VALUES (?,?,?,?,?,?,?)",
        (ts.isoformat(), status, stats["ok"], stats["signals"], stats["accepted"], stats["rejected"], stats["errors"]),
    )
    conn.commit()

    # File
    hb_dir = cfg["heartbeat_dir"]
    hb_dir.mkdir(parents=True, exist_ok=True)
    path = hb_dir / f"{ts.strftime('%Y%m%d_%H%M')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# [{session_key}] Heartbeat {ts.strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write(f"Status: **{status}** | disk={disk_free}GB\n\n")
        for k, v in stats.items():
            f.write(f"- {k}: {v}\n")

    return status


# ── Main ──


async def main(duration_min: int = 480):
    """Run 3 independent sessions."""
    logger.info("=" * 60)
    logger.info("v2.5A 分层 Paper Monitor — 3 Sessions")
    logger.info(f"Duration: {duration_min}min | Scan: {SCAN_INTERVAL}s | HB: {HEARTBEAT_INTERVAL}s")
    logger.info("=" * 60)

    # Init DBs
    conns = {}
    for key, cfg in SESSIONS.items():
        cfg["output_dir"].mkdir(parents=True, exist_ok=True)
        conns[key] = init_db(cfg["db_path"], cfg["full_tracking"])
        logger.info(f"  {cfg['name']}: {len(cfg['symbols'])} symbols")

    start_time = time.time()
    end_time = start_time + duration_min * 60
    last_hb = 0
    consecutive_fails = {k: 0 for k in SESSIONS}
    scan_count = 0

    async with CCXTClient() as client:
        # API check
        try:
            await client.fetch_ohlcv("BTC/USDT", "5m", limit=1)
            logger.info("API: OK")
        except Exception as e:
            logger.error(f"API FAIL: {e}")
            return

        while time.time() < end_time:
            t0 = time.time()

            # Stop conditions
            disk = shutil.disk_usage(".")
            if disk.free / (1024**3) < MIN_DISK_FREE_GB:
                logger.error(f"STOP: disk < {MIN_DISK_FREE_GB}GB")
                break

            if any(v >= MAX_FAILED_HEARTBEATS for v in consecutive_fails.values()):
                failed_session = [k for k, v in consecutive_fails.items() if v >= MAX_FAILED_HEARTBEATS][0]
                logger.error(f"STOP: {failed_session} had {MAX_FAILED_HEARTBEATS} consecutive failures")
                break

            # Bar delay check
            bar_delay = 0.0
            try:
                c = await client.fetch_ohlcv("BTC/USDT", "5m", limit=1)
                if c:
                    bar_delay = (time.time() * 1000 - c[-1][0]) / 300_000
            except Exception:
                bar_delay = 99.0
            paused = bar_delay > MAX_BAR_DELAY_BARS

            # Scan all 3 sessions
            all_stats = {}
            for key, cfg in SESSIONS.items():
                stats = await scan_session(client, key, cfg, conns[key], paused)
                all_stats[key] = stats

            scan_count += 1
            elapsed = time.time() - t0
            total_sig = sum(s["signals"] for s in all_stats.values())
            total_acc = sum(s["accepted"] for s in all_stats.values())
            total_err = sum(s["errors"] for s in all_stats.values())
            logger.info(
                f"Scan #{scan_count} ({elapsed:.1f}s) | "
                f"core={all_stats['core']['signals']}/{all_stats['core']['ok']} "
                f"cand={all_stats['candidate']['signals']}/{all_stats['candidate']['ok']} "
                f"broad={all_stats['broad']['signals']}/{all_stats['broad']['ok']} "
                f"| total sig={total_sig} acc={total_acc} err={total_err}"
            )

            # Heartbeat
            if time.time() - last_hb >= HEARTBEAT_INTERVAL:
                for key, cfg in SESSIONS.items():
                    status = save_heartbeat(key, cfg, conns[key], all_stats[key])
                    if status == "failed":
                        consecutive_fails[key] += 1
                    else:
                        consecutive_fails[key] = 0

                    # DB write test
                    try:
                        conns[key].execute("INSERT INTO heartbeats (ts, status) VALUES ('_test','_test')")
                        conns[key].execute("DELETE FROM heartbeats WHERE ts='_test'")
                        conns[key].commit()
                    except Exception:
                        logger.error(f"STOP: {key} DB write failed")
                        for c in conns.values():
                            c.close()
                        return

                last_hb = time.time()

            # Sleep
            sleep_time = max(0, SCAN_INTERVAL - (time.time() - t0))
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    # Final reports
    for key, cfg in SESSIONS.items():
        _write_daily_summary(key, cfg, conns[key], scan_count)
        conns[key].close()

    logger.info(f"Monitor stopped after {scan_count} scans")


def _write_daily_summary(key: str, cfg: dict, conn: sqlite3.Connection, scans: int) -> None:
    """Write per-session daily summary."""
    report_dir = Path(f"reports/v2_5A_top50_paper_observation/daily/{key}")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%d")
    path = report_dir / f"{ts}_summary.md"

    sig_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    accepted = conn.execute("SELECT COUNT(*) FROM signals WHERE status='accepted'").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM signals WHERE status='rejected'").fetchone()[0]
    hb_count = conn.execute("SELECT COUNT(*) FROM heartbeats WHERE ts != '_test'").fetchone()[0]
    hb_failed = conn.execute("SELECT COUNT(*) FROM heartbeats WHERE status='failed' AND ts != '_test'").fetchone()[0]

    trade_info = ""
    if cfg["full_tracking"]:
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        pos_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        trade_info = f"| Trades completed | {trade_count} |\n| Open positions | {pos_count} |\n"
        if trade_count > 0:
            wins = conn.execute("SELECT COUNT(*) FROM trades WHERE pnl_pct > 0").fetchone()[0]
            avg_pnl = conn.execute("SELECT AVG(pnl_pct) FROM trades").fetchone()[0] or 0
            trade_info += f"| Win rate | {wins}/{trade_count} ({wins / trade_count * 100:.1f}%) |\n"
            trade_info += f"| Avg PnL | {avg_pnl:+.2f}% |\n"

    # Signals by symbol
    rows = conn.execute(
        "SELECT symbol, COUNT(*), SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) "
        "FROM signals GROUP BY symbol ORDER BY COUNT(*) DESC"
    ).fetchall()

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# [{key.upper()}] Daily Summary — {ts}\n\n")
        f.write(f"**{cfg['name']}** | {len(cfg['symbols'])} symbols\n\n")
        f.write("**No strategy conclusions. Observation only.**\n\n")
        f.write("| Metric | Value |\n|--------|-------|\n")
        f.write(f"| Scans | {scans} |\n")
        f.write(f"| Heartbeats | {hb_count} ({hb_failed} failed) |\n")
        f.write(f"| Total signals | {sig_count} |\n")
        f.write(f"| Accepted | {accepted} |\n")
        f.write(f"| Rejected | {rejected} |\n")
        f.write(trade_info)
        if rows:
            f.write("\n## Signals by Symbol\n\n| Symbol | Total | Accepted |\n|--------|-------|----------|\n")
            for r in rows:
                f.write(f"| {r[0]} | {r[1]} | {r[2]} |\n")

    logger.info(f"  [{key}] Daily summary: {path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="v2.5A 分层 Paper Monitor")
    parser.add_argument("--duration", type=int, default=480, help="Duration in minutes (default 480)")
    args = parser.parse_args()
    asyncio.run(main(duration_min=args.duration))
