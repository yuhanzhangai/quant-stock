"""纸上交易追踪器：记录每个信号，追踪虚拟 P&L。

不是自动交易！而是：
1. 每 5 分钟扫描信号
2. 记录入场/出场到 SQLite
3. 追踪每笔虚拟交易的盈亏
4. 每天生成 P&L 报告

用来验证 MinSwing v3 在实时市场中的表现。
"""

import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.strategies.minswing_v3_final import minswing_v3_signal

COINS = {
    "ETH/USDT": "ETH",
    "SOL/USDT": "SOL",
    "NEAR/USDT": "NEAR",
    "ARB/USDT": "ARB",
}
LEVERAGE = 5
CAPITAL_PER_COIN = 12.50  # $50 / 4 coins


class PaperTrader:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._create_tables()

    def _create_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, side TEXT, price REAL,
                timestamp TEXT, signal_type TEXT,
                position_size REAL, leverage INTEGER
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL, entry_time TEXT,
                peak_price REAL, side TEXT
            )
        """)
        self._conn.commit()

    def record_entry(self, symbol: str, price: float, side: str = "LONG"):
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO trades (symbol, side, price, timestamp, signal_type, position_size, leverage) VALUES (?,?,?,?,?,?,?)",
            (symbol, side, price, now, "ENTRY", CAPITAL_PER_COIN * LEVERAGE, LEVERAGE),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO positions (symbol, entry_price, entry_time, peak_price, side) VALUES (?,?,?,?,?)",
            (symbol, price, now, price, side),
        )
        self._conn.commit()
        logger.info(f"PAPER ENTRY | {symbol} {side} @ ${price:,.2f} | size: ${CAPITAL_PER_COIN * LEVERAGE:,.2f}")

    def record_exit(self, symbol: str, price: float):
        pos = self._conn.execute("SELECT entry_price, side FROM positions WHERE symbol=?", (symbol,)).fetchone()
        if not pos:
            return

        entry_price, side = pos
        if side == "LONG":
            pnl_pct = (price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - price) / entry_price * 100

        pnl_usd = CAPITAL_PER_COIN * LEVERAGE * pnl_pct / 100

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO trades (symbol, side, price, timestamp, signal_type, position_size, leverage) VALUES (?,?,?,?,?,?,?)",
            (symbol, "CLOSE", price, now, "EXIT", 0, LEVERAGE),
        )
        self._conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        self._conn.commit()
        logger.info(f"PAPER EXIT  | {symbol} @ ${price:,.2f} | P&L: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")

    def has_position(self, symbol: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM positions WHERE symbol=?", (symbol,)).fetchone()
        return row is not None

    def get_summary(self) -> str:
        trades = self._conn.execute("SELECT * FROM trades ORDER BY timestamp").fetchall()
        positions = self._conn.execute("SELECT * FROM positions").fetchall()
        return f"Total trades: {len(trades)}, Open positions: {len(positions)}"


async def scan_and_trade(trader: PaperTrader):
    settings = get_settings()

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        logger.info(f"\n--- Paper scan {time.strftime('%H:%M:%S UTC')} ---")

        for symbol, coin in COINS.items():
            try:
                candles = await client.fetch_ohlcv_range(
                    symbol, timeframe="5m",
                    since=int(time.time() * 1000) - 300 * 5 * 60 * 1000,
                )
                if len(candles) < 200:
                    continue

                df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                price = df.set_index("datetime")["close"]

                entries, exits = minswing_v3_signal(price, coin=coin)
                current = price.iloc[-1]

                recent_entry = entries.iloc[-3:].any()
                recent_exit = exits.iloc[-3:].any()

                if recent_entry and not trader.has_position(symbol):
                    trader.record_entry(symbol, current)
                elif recent_exit and trader.has_position(symbol):
                    trader.record_exit(symbol, current)

            except Exception as e:
                logger.error(f"{symbol}: {e}")

        logger.info(f"Status: {trader.get_summary()}")


async def main():
    db_path = Path("data") / "paper_trades.sqlite"
    db_path.parent.mkdir(exist_ok=True)
    trader = PaperTrader(db_path)

    logger.info("Paper Trader 启动 | MinSwing v3 | $50 x 5x")
    logger.info(f"Coins: {list(COINS.keys())}")

    if "--once" in sys.argv:
        await scan_and_trade(trader)
    else:
        while True:
            await scan_and_trade(trader)
            await asyncio.sleep(300)


if __name__ == "__main__":
    asyncio.run(main())
