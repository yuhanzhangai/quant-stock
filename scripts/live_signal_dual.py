"""双向实时信号：做多 + 做空信号都生成。

上升趋势 -> 做多信号（MinSwing 原版）
下降趋势 -> 做空信号（ShortSwing）
横盘 -> 不交易
"""

import asyncio
import sys
import time
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.strategies.minute_swing import MinuteSwingStrategy
from src.strategies.short_swing import ShortSwingStrategy

COINS = ["ETH/USDT", "SOL/USDT", "NEAR/USDT", "ARB/USDT"]
LONG_PARAMS = {"trend_ma": 180, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 144}
SHORT_PARAMS = {"trend_ma": 180, "rsi_entry": 60, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 288}
LEVERAGE = 5
CAPITAL = 50.0


async def scan() -> None:
    settings = get_settings()
    long_strat = MinuteSwingStrategy()
    short_strat = ShortSwingStrategy()

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        logger.info(f"\n{'=' * 65}")
        logger.info(f"双向信号扫描 | ${CAPITAL} x {LEVERAGE}x | {time.strftime('%H:%M UTC')}")
        logger.info(f"{'=' * 65}")

        for symbol in COINS:
            try:
                candles = await client.fetch_ohlcv_range(
                    symbol,
                    timeframe="5m",
                    since=int(time.time() * 1000) - 300 * 5 * 60 * 1000,
                )
                if len(candles) < 200:
                    continue

                df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                price = df.set_index("datetime")["close"]

                current = price.iloc[-1]
                ma = price.rolling(window=180).mean()
                trend = "UP" if current > ma.iloc[-1] else "DOWN"

                # RSI
                delta = price.diff()
                gains = delta.clip(lower=0).rolling(window=14).mean()
                losses = (-delta).clip(lower=0).rolling(window=14).mean()
                rs = gains / losses
                rsi = (100 - (100 / (1 + rs))).iloc[-1]

                # 做多信号
                long_e, long_x = long_strat.generate_signals(price, **LONG_PARAMS)
                long_entry = long_e.iloc[-3:].any()
                long_exit = long_x.iloc[-3:].any()

                # 做空信号
                short_e, short_x = short_strat.generate_signals(price)
                short_entry = short_e.iloc[-3:].any()
                short_exit = short_x.iloc[-3:].any()

                # 价位计算
                sl_long = current * (1 - LONG_PARAMS["stop_pct"] / 100)
                tp_long = current * (1 + LONG_PARAMS["take_profit_pct"] / 100)
                sl_short = current * (1 + SHORT_PARAMS["stop_pct"] / 100)
                tp_short = current * (1 - SHORT_PARAMS["take_profit_pct"] / 100)

                logger.info(f"\n  {symbol:10s} | ${current:,.2f} | trend: {trend} | RSI: {rsi:.0f}")

                if long_entry:
                    logger.info(f"  {'':10s} | >>> LONG ENTRY <<< buy @ ${current:,.2f}")
                    logger.info(f"  {'':10s} | SL: ${sl_long:,.2f} | TP: ${tp_long:,.2f}")
                elif short_entry:
                    logger.info(f"  {'':10s} | >>> SHORT ENTRY <<< sell @ ${current:,.2f}")
                    logger.info(f"  {'':10s} | SL: ${sl_short:,.2f} | TP: ${tp_short:,.2f}")
                elif long_exit:
                    logger.info(f"  {'':10s} | >>> CLOSE LONG <<<")
                elif short_exit:
                    logger.info(f"  {'':10s} | >>> CLOSE SHORT <<<")
                else:
                    logger.info(f"  {'':10s} | waiting ({trend} trend)")

            except Exception as e:
                logger.error(f"  {symbol}: {e}")


async def main() -> None:
    logger.info("双向信号监控启动 (Ctrl+C 停止)")
    while True:
        await scan()
        logger.info("\n下次: 5分钟后...")
        await asyncio.sleep(300)


if __name__ == "__main__":
    if "--once" in sys.argv:
        asyncio.run(scan())
    else:
        asyncio.run(main())
