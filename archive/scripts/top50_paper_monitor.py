"""Top50 Paper Observation — Overnight Monitor Mode.

每 5 分钟扫描信号，每 30 分钟生成 heartbeat。
连续 3 次 heartbeat failed 自动停止。

Usage:
    .venv/Scripts/python.exe scripts/top50_paper_monitor.py
    .venv/Scripts/python.exe scripts/top50_paper_monitor.py --duration 480  # 8 hours
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
import yaml
from loguru import logger

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.storage.parquet_writer import ParquetWriter
from src.strategies.minswing_v3_final import MinSwingV3Strategy

# ── Config ──

SCAN_INTERVAL = 300  # 5 minutes
HEARTBEAT_INTERVAL = 1800  # 30 minutes
MAX_FAILED_HEARTBEATS = 3
MAX_BAR_DELAY_BARS = 2
MIN_DISK_FREE_GB = 5
MIN_BARS_FOR_STRATEGY = 200

UNIVERSE_PATH = Path("config/universe/okx_top50_20260424.yml")
HEARTBEAT_DIR = Path("reports/v2_5A_top50_paper_observation/heartbeat")
SESSION_DB = Path("data/top50_paper_session.sqlite")

# Coins that have per-coin exit config
CONFIGURED_COINS = {"ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"}


# ── Session DB ──


def init_session_db() -> sqlite3.Connection:
    """Initialize local session tracking DB."""
    SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SESSION_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, side TEXT, price REAL,
            confidence TEXT, status TEXT, reject_reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, status TEXT, api_health TEXT,
            latest_bar_delay_max REAL, symbols_updated INTEGER,
            db_write_ok INTEGER, active_positions INTEGER,
            signal_count INTEGER, accepted_count INTEGER,
            rejected_count INTEGER, error_count INTEGER,
            disk_free_gb REAL, memory_mb REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


# ── Load Universe ──


def load_universe() -> list[str]:
    """Load selected symbols from universe file."""
    with open(UNIVERSE_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    symbols = []
    for entry in data.get("selected", []):
        if isinstance(entry, dict):
            symbols.append(entry["symbol"])
    return symbols


# ── Signal Scanner ──


async def scan_signals(
    client: CCXTClient,
    writer: ParquetWriter,
    symbols: list[str],
    conn: sqlite3.Connection,
    bar_delay_paused: bool,
) -> dict:
    """Scan all symbols for MinSwing signals."""
    strat = MinSwingV3Strategy()
    stats = {"signals": 0, "accepted": 0, "rejected": 0, "errors": 0, "symbols_ok": 0}
    now_ts = datetime.now(tz=UTC).isoformat()

    for symbol in symbols:
        ccxt_sym = symbol.replace("-", "/")
        try:
            # Fetch latest candles
            candles = await client.fetch_ohlcv(ccxt_sym, "5m", limit=300)
            if not candles or len(candles) < MIN_BARS_FOR_STRATEGY:
                continue

            pdf = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
            pdf["datetime"] = pd.to_datetime(pdf["ts"], unit="ms", utc=True)
            price = pdf.set_index("datetime")["close"]

            coin = symbol.replace("-USDT", "")
            entries, _ = strat.generate_signals(price, coin=coin)

            # Check last 3 bars for signal
            recent_entry = entries.iloc[-3:].any()
            if recent_entry:
                stats["signals"] += 1
                current_price = float(price.iloc[-1])

                if bar_delay_paused:
                    status = "rejected"
                    reason = "bar_delay_paused"
                    stats["rejected"] += 1
                else:
                    status = "accepted"
                    reason = ""
                    stats["accepted"] += 1

                conn.execute(
                    "INSERT INTO signals (ts, symbol, side, price, confidence, status, reject_reason) "
                    "VALUES (?, ?, 'long', ?, 'MED', ?, ?)",
                    (now_ts, symbol, current_price, status, reason),
                )

            stats["symbols_ok"] += 1

        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 3:
                logger.warning(f"  {symbol}: {str(e)[:60]}")

    conn.commit()
    return stats


# ── Heartbeat ──


def generate_heartbeat(
    api_ok: bool,
    max_delay: float,
    symbols_updated: int,
    db_ok: bool,
    scan_stats: dict,
    error_count: int,
) -> dict:
    """Generate heartbeat report."""
    disk = shutil.disk_usage(".")
    disk_free_gb = round(disk.free / (1024**3), 1)

    try:
        import psutil

        memory_mb = round(psutil.Process().memory_info().rss / (1024**2), 1)
    except ImportError:
        memory_mb = 0.0

    # Determine status
    if not api_ok or not db_ok or disk_free_gb < MIN_DISK_FREE_GB:
        status = "failed"
    elif max_delay > MAX_BAR_DELAY_BARS or error_count > 5:
        status = "warning"
    else:
        status = "normal"

    return {
        "ts": datetime.now(tz=UTC).isoformat(),
        "status": status,
        "api_health": "OK" if api_ok else "FAIL",
        "latest_bar_delay_max": round(max_delay, 1),
        "symbols_updated": symbols_updated,
        "db_write_ok": db_ok,
        "active_positions": 0,
        "signal_count": scan_stats.get("signals", 0),
        "accepted_count": scan_stats.get("accepted", 0),
        "rejected_count": scan_stats.get("rejected", 0),
        "error_count": error_count,
        "disk_free_gb": disk_free_gb,
        "memory_mb": memory_mb,
    }


def save_heartbeat(hb: dict, conn: sqlite3.Connection) -> None:
    """Save heartbeat to DB + file."""
    # DB
    conn.execute(
        "INSERT INTO heartbeats (ts, status, api_health, latest_bar_delay_max, "
        "symbols_updated, db_write_ok, active_positions, signal_count, "
        "accepted_count, rejected_count, error_count, disk_free_gb, memory_mb) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            hb["ts"],
            hb["status"],
            hb["api_health"],
            hb["latest_bar_delay_max"],
            hb["symbols_updated"],
            1 if hb["db_write_ok"] else 0,
            hb["active_positions"],
            hb["signal_count"],
            hb["accepted_count"],
            hb["rejected_count"],
            hb["error_count"],
            hb["disk_free_gb"],
            hb["memory_mb"],
        ),
    )
    conn.commit()

    # File
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC)
    filename = f"{ts.strftime('%Y%m%d_%H%M')}.md"
    path = HEARTBEAT_DIR / filename

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Heartbeat {ts.strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write(f"Status: **{hb['status']}**\n\n")
        for k, v in hb.items():
            f.write(f"- {k}: {v}\n")

    icon = {"normal": "✓", "warning": "⚠", "failed": "✗"}.get(hb["status"], "?")
    logger.info(
        f"[{icon}] Heartbeat {hb['status']} | "
        f"signals={hb['signal_count']} accepted={hb['accepted_count']} "
        f"rejected={hb['rejected_count']} errors={hb['error_count']} "
        f"disk={hb['disk_free_gb']}GB"
    )


# ── Check Bar Delay ──


async def check_bar_delay(client: CCXTClient) -> float:
    """Check max bar delay across key symbols."""
    max_delay = 0.0
    for sym in ["BTC/USDT", "ETH/USDT"]:
        try:
            candles = await client.fetch_ohlcv(sym, "5m", limit=1)
            if candles:
                latest_ms = candles[-1][0]
                now_ms = int(time.time() * 1000)
                delay_bars = (now_ms - latest_ms) / 300_000
                max_delay = max(max_delay, delay_bars)
        except Exception:
            max_delay = 99.0
    return max_delay


# ── Main Loop ──


async def main(duration_min: int = 480):
    """Main overnight monitor loop."""
    logger.info("=" * 60)
    logger.info("Top50 Paper Observation — Overnight Monitor")
    logger.info(f"Duration: {duration_min} min | Scan: {SCAN_INTERVAL}s | Heartbeat: {HEARTBEAT_INTERVAL}s")
    logger.info("=" * 60)

    symbols = load_universe()
    logger.info(f"Universe: {len(symbols)} symbols")

    conn = init_session_db()
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)

    start_time = time.time()
    end_time = start_time + duration_min * 60
    last_heartbeat = 0
    consecutive_failures = 0
    total_scans = 0
    cumulative_stats = {"signals": 0, "accepted": 0, "rejected": 0, "errors": 0}

    async with CCXTClient() as client:
        # Initial API check
        try:
            await client.fetch_ohlcv("BTC/USDT", "5m", limit=1)
            logger.info("API health: OK")
        except Exception as e:
            logger.error(f"API health: FAIL — {e}")
            return

        while time.time() < end_time:
            loop_start = time.time()

            # Check stop conditions
            disk = shutil.disk_usage(".")
            if disk.free / (1024**3) < MIN_DISK_FREE_GB:
                logger.error(f"STOP: disk_free < {MIN_DISK_FREE_GB}GB")
                break

            if consecutive_failures >= MAX_FAILED_HEARTBEATS:
                logger.error(f"STOP: {MAX_FAILED_HEARTBEATS} consecutive heartbeat failures")
                break

            # Check bar delay
            bar_delay = await check_bar_delay(client)
            bar_delay_paused = bar_delay > MAX_BAR_DELAY_BARS

            if bar_delay_paused:
                logger.warning(f"Bar delay {bar_delay:.1f} > {MAX_BAR_DELAY_BARS} — pausing accepted trades")

            # Scan signals
            stats = {"signals": 0, "accepted": 0, "rejected": 0, "errors": 0, "symbols_ok": 0}
            try:
                stats = await scan_signals(client, writer, symbols, conn, bar_delay_paused)
                total_scans += 1
                for k in cumulative_stats:
                    cumulative_stats[k] += stats.get(k, 0)

                elapsed = time.time() - loop_start
                logger.info(
                    f"Scan #{total_scans} ({elapsed:.1f}s) | "
                    f"signals={stats['signals']} accepted={stats['accepted']} "
                    f"errors={stats['errors']} | {stats['symbols_ok']}/{len(symbols)} symbols"
                )
            except Exception as e:
                logger.error(f"Scan error: {e}")
                cumulative_stats["errors"] += 1

            # Heartbeat (every 30 min)
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                api_ok = True
                try:
                    await client.fetch_ohlcv("BTC/USDT", "5m", limit=1)
                except Exception:
                    api_ok = False

                db_ok = True
                try:
                    conn.execute("INSERT INTO heartbeats (ts, status) VALUES ('_db_test', '_test')")
                    conn.execute("DELETE FROM heartbeats WHERE ts='_db_test'")
                    conn.commit()
                except Exception:
                    db_ok = False
                    logger.error("STOP: DB write failed")
                    break

                hb = generate_heartbeat(
                    api_ok,
                    bar_delay,
                    stats.get("symbols_ok", 0),
                    db_ok,
                    cumulative_stats,
                    cumulative_stats["errors"],
                )
                save_heartbeat(hb, conn)

                if hb["status"] == "failed":
                    consecutive_failures += 1
                    logger.warning(f"Heartbeat FAILED ({consecutive_failures}/{MAX_FAILED_HEARTBEATS})")
                else:
                    consecutive_failures = 0

                last_heartbeat = time.time()
                # Reset cumulative for next period
                cumulative_stats = {"signals": 0, "accepted": 0, "rejected": 0, "errors": 0}

            # Wait for next scan
            sleep_time = max(0, SCAN_INTERVAL - (time.time() - loop_start))
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    # Final status
    conn.close()
    logger.info("=" * 60)
    logger.info(f"Monitor stopped after {total_scans} scans")

    # Generate overnight status report
    _write_overnight_report(total_scans, consecutive_failures)


def _write_overnight_report(total_scans: int, failures: int) -> None:
    """Generate overnight_status_report.md."""
    report_dir = Path("reports/v2_5A_top50_paper_observation")
    report_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(tz=UTC).strftime("%Y%m%d")
    path = report_dir / f"overnight_status_{ts}.md"

    conn = sqlite3.connect(str(SESSION_DB))
    signal_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    accepted = conn.execute("SELECT COUNT(*) FROM signals WHERE status='accepted'").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM signals WHERE status='rejected'").fetchone()[0]
    hb_count = conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
    hb_failed = conn.execute("SELECT COUNT(*) FROM heartbeats WHERE status='failed'").fetchone()[0]

    # Signals by symbol
    rows = conn.execute(
        "SELECT symbol, COUNT(*) as cnt, SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) as acc "
        "FROM signals GROUP BY symbol ORDER BY cnt DESC"
    ).fetchall()
    conn.close()

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Overnight Status Report — {ts}\n\n")
        f.write("**No strategy conclusions. Observation only.**\n\n")
        f.write("| Metric | Value |\n|--------|-------|\n")
        f.write(f"| Total scans | {total_scans} |\n")
        f.write(f"| Heartbeats | {hb_count} ({hb_failed} failed) |\n")
        f.write(f"| Total signals | {signal_count} |\n")
        f.write(f"| Accepted | {accepted} |\n")
        f.write(f"| Rejected | {rejected} |\n")
        f.write(f"| Consecutive failures at stop | {failures} |\n\n")

        if rows:
            f.write("## Signals by Symbol\n\n")
            f.write("| Symbol | Total | Accepted |\n|--------|-------|----------|\n")
            for r in rows:
                f.write(f"| {r[0]} | {r[1]} | {r[2]} |\n")

    logger.info(f"Overnight report: {path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Top50 Paper Observation Monitor")
    parser.add_argument("--duration", type=int, default=480, help="Duration in minutes (default: 480 = 8h)")
    args = parser.parse_args()

    asyncio.run(main(duration_min=args.duration))
