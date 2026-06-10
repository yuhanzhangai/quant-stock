"""拉取 Top50 新币 90 天 5m 数据。

Usage:
    python scripts/fetch_top50_data.py
"""

import asyncio
import sys
import time
from pathlib import Path

import polars as pl
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.exchange.ccxt_client import CCXTClient
from src.storage.parquet_writer import ParquetWriter

NEW_COINS = [
    "BASED/USDT", "SPK/USDT", "TRX/USDT", "ORDI/USDT", "HYPE/USDT", "ZEC/USDT",
    "ENJ/USDT", "STRK/USDT", "BNB/USDT", "TON/USDT", "CHZ/USDT", "IP/USDT",
    "PENGU/USDT", "WIF/USDT", "XLM/USDT", "TRUMP/USDT", "XPL/USDT", "OKB/USDT",
    "LTC/USDT", "PI/USDT", "ENA/USDT", "PYTH/USDT", "CFX/USDT", "POL/USDT",
    "LIT/USDT", "PUMP/USDT", "ONDO/USDT", "ROBO/USDT", "DYDX/USDT", "MASK/USDT",
    "BCH/USDT", "APE/USDT",
]

SINCE_MS = int((time.time() - 90 * 86400) * 1000)


async def main():
    writer = ParquetWriter(Path("data/parquet"))
    total = len(NEW_COINS)

    async with CCXTClient() as client:
        for idx, symbol in enumerate(NEW_COINS):
            sym_key = symbol.replace("/", "-")
            t0 = time.time()
            try:
                candles = await client.fetch_ohlcv_range(symbol, "5m", since=SINCE_MS)
                elapsed = time.time() - t0

                if not candles or len(candles) < 10:
                    logger.warning(f"[{idx+1}/{total}] {sym_key}: insufficient data ({len(candles) if candles else 0})")
                    continue

                new_df = pl.DataFrame({
                    "timestamp": [c[0] for c in candles],
                    "open": [c[1] for c in candles],
                    "high": [c[2] for c in candles],
                    "low": [c[3] for c in candles],
                    "close": [c[4] for c in candles],
                    "volume": [c[5] for c in candles],
                })
                writer.write_ohlcv(new_df, sym_key, "5m")
                logger.info(f"[{idx+1}/{total}] {sym_key}: {len(candles)} candles ({elapsed:.1f}s)")

            except Exception as e:
                logger.error(f"[{idx+1}/{total}] {sym_key}: {e}")

    logger.success(f"Done. {total} coins processed.")


if __name__ == "__main__":
    asyncio.run(main())
