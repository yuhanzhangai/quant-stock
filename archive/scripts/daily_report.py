"""每日策略报告：总结当天信号、市场状态、收益估算。"""

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

COINS = ["ETH/USDT", "SOL/USDT", "NEAR/USDT", "ARB/USDT"]
PARAMS = {"trend_ma": 180, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 144}


async def generate_report() -> None:
    settings = get_settings()
    strat = MinuteSwingStrategy()

    logger.info("=" * 60)
    logger.info(f"每日策略报告 | {time.strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        for symbol in COINS:
            try:
                candles = await client.fetch_ohlcv_range(
                    symbol,
                    timeframe="5m",
                    since=int(time.time() * 1000) - 300 * 5 * 60 * 1000,
                )
                if len(candles) < 200:
                    continue

                df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                price = df.set_index("datetime")["close"]

                current = price.iloc[-1]
                h24_ago = price.iloc[-288] if len(price) > 288 else price.iloc[0]
                change_24h = (current - h24_ago) / h24_ago * 100

                ma = price.rolling(window=180).mean()
                trend = "UP" if current > ma.iloc[-1] else "DOWN"

                delta = price.diff()
                g = delta.clip(lower=0).rolling(14).mean()
                l = (-delta).clip(lower=0).rolling(14).mean()
                rsi = (100 - 100 / (1 + g / l)).iloc[-1]

                entries, exits = strat.generate_signals(price, **PARAMS)
                n_entries = entries.iloc[-288:].sum() if len(entries) > 288 else entries.sum()
                n_exits = exits.iloc[-288:].sum() if len(exits) > 288 else exits.sum()

                # 最近信号
                last_entry_idx = entries[entries].index[-1] if entries.any() else None
                last_exit_idx = exits[exits].index[-1] if exits.any() else None

                logger.info(f"\n  {symbol}")
                logger.info(f"    价格: ${current:,.2f} | 24h: {change_24h:+.2f}%")
                logger.info(f"    趋势: {trend} | RSI: {rsi:.0f}")
                logger.info(f"    24h 信号: {n_entries} 入场 / {n_exits} 出场")
                if last_entry_idx:
                    logger.info(f"    最近入场: {last_entry_idx}")
                if last_exit_idx:
                    logger.info(f"    最近出场: {last_exit_idx}")

                # 建议
                if trend == "UP" and rsi < 50:
                    logger.info("    状态: 准备入场（趋势向上+RSI 低位）")
                elif trend == "UP" and rsi > 70:
                    logger.info("    状态: 谨慎（趋势向上但 RSI 超买）")
                elif trend == "DOWN":
                    logger.info("    状态: 等待（下降趋势，不做多）")

            except Exception as e:
                logger.error(f"  {symbol}: {e}")

    logger.info(f"\n{'=' * 60}")
    logger.info("策略参数: tm=180 sl=2% tp=8% gap=144 (12h)")
    logger.info("建议杠杆: 5x | 建议资金: $50 分散 4 币种")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(generate_report())
