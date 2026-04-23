"""1m 超精确信号生成器。

1m 数据入场点更精准，但使用与 5m 相同的慢参数。
本质：15 小时趋势确认 + 12 小时间隔 + 5% 止盈。
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

COINS = {
    "ETH/USDT": {"trend_ma": 900, "stop_pct": 1.5, "take_profit_pct": 5.0, "min_gap": 720},
    "SOL/USDT": {"trend_ma": 900, "stop_pct": 1.5, "take_profit_pct": 5.0, "min_gap": 720},
}
LEVERAGE = 5
CAPITAL = 50.0


async def scan() -> None:
    settings = get_settings()
    strat = MinuteSwingStrategy()

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        logger.info(f"\n{'='*60}")
        logger.info(f"1m 信号扫描 | {time.strftime('%H:%M:%S UTC')}")
        logger.info(f"{'='*60}")

        for symbol, params in COINS.items():
            try:
                candles = await client.fetch_ohlcv_range(
                    symbol, timeframe="1m",
                    since=int(time.time() * 1000) - 1000 * 60 * 1000,
                )
                if len(candles) < 900:
                    logger.warning(f"{symbol}: 数据不足 ({len(candles)} bars, need 900)")
                    continue

                df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                price = df.set_index("datetime")["close"]

                entries, exits = strat.generate_signals(price, **params)
                current = price.iloc[-1]
                recent_entry = entries.iloc[-3:].any()
                recent_exit = exits.iloc[-3:].any()

                ma = price.rolling(window=params["trend_ma"]).mean()
                trend = "UP" if current > ma.iloc[-1] else "DOWN"

                delta = price.diff()
                g = delta.clip(lower=0).rolling(14).mean()
                l = (-delta).clip(lower=0).rolling(14).mean()
                rsi = (100 - 100 / (1 + g / l)).iloc[-1]

                logger.info(f"\n  {symbol:10s} | ${current:,.2f} | trend: {trend} | RSI: {rsi:.0f}")

                if recent_entry:
                    sl = current * (1 - params["stop_pct"] / 100)
                    tp = current * (1 + params["take_profit_pct"] / 100)
                    logger.info(f"  {'':10s} | >>> 1m ENTRY <<< @ ${current:,.2f}")
                    logger.info(f"  {'':10s} | SL: ${sl:,.2f} | TP: ${tp:,.2f}")
                elif recent_exit:
                    logger.info(f"  {'':10s} | >>> EXIT <<<")
                else:
                    logger.info(f"  {'':10s} | waiting")

            except Exception as e:
                logger.error(f"{symbol}: {e}")


if __name__ == "__main__":
    asyncio.run(scan())
