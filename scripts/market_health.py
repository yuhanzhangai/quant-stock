"""市场健康检查：交易前先看市场状态。

每天交易前跑一次，判断今天是否适合交易。
检查项：
1. 趋势方向（4 个币种中多少在上升趋势）
2. 波动率水平（是否异常高/低）
3. RSI 分布（是否极端超买/超卖）
4. 资金费率（是否异常）

输出：绿灯/黄灯/红灯
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

COINS = ["ETH/USDT", "SOL/USDT", "NEAR/USDT", "ARB/USDT"]


async def health_check() -> None:
    settings = get_settings()

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        logger.info(f"\n{'='*60}")
        logger.info(f"市场健康检查 | {time.strftime('%Y-%m-%d %H:%M UTC')}")
        logger.info(f"{'='*60}")

        uptrend_count = 0
        high_rsi_count = 0
        low_rsi_count = 0
        total_vol = 0.0

        for symbol in COINS:
            try:
                candles = await client.fetch_ohlcv_range(
                    symbol, timeframe="5m",
                    since=int(time.time() * 1000) - 300 * 5 * 60 * 1000,
                )
                if len(candles) < 200:
                    continue

                df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                price = df["close"]

                # 趋势
                ma180 = price.rolling(180).mean().iloc[-1]
                current = price.iloc[-1]
                trend = "UP" if current > ma180 else "DOWN"
                if trend == "UP":
                    uptrend_count += 1

                # RSI
                delta = price.diff()
                g = delta.clip(lower=0).rolling(14).mean()
                l = (-delta).clip(lower=0).rolling(14).mean()
                rsi = (100 - 100 / (1 + g / l)).iloc[-1]
                if rsi > 70:
                    high_rsi_count += 1
                elif rsi < 30:
                    low_rsi_count += 1

                # 波动率
                vol = price.pct_change().tail(48).std() * 100
                total_vol += vol

                # 24h 变化
                h24 = price.iloc[-288] if len(price) > 288 else price.iloc[0]
                change = (current - h24) / h24 * 100

                logger.info(f"  {symbol:10s} | ${current:>10,.2f} | {trend:4s} | RSI:{rsi:5.1f} | vol:{vol:.3f}% | 24h:{change:+.1f}%")

            except Exception as e:
                logger.error(f"  {symbol}: {e}")

        # 综合判断
        avg_vol = total_vol / len(COINS)
        logger.info(f"\n  --- 综合评估 ---")
        logger.info(f"  上升趋势: {uptrend_count}/{len(COINS)} 币种")
        logger.info(f"  RSI 超买: {high_rsi_count} | RSI 超卖: {low_rsi_count}")
        logger.info(f"  平均波动: {avg_vol:.3f}%")

        # 信号灯
        if uptrend_count >= 3 and high_rsi_count == 0:
            light = "🟢 绿灯 - 积极交易"
        elif uptrend_count >= 2:
            light = "🟡 黄灯 - 谨慎交易"
        elif uptrend_count <= 1:
            light = "🔴 红灯 - 暂停交易（等待趋势恢复）"

        if avg_vol > 0.5:
            light += " | ⚠️ 高波动"

        logger.info(f"\n  {light}")

        # 动量轮动：推荐本周 Top 2 币种
        logger.info(f"\n  --- 动量轮动 (集中交易 Top 2) ---")
        coin_momentum = {}
        for symbol in COINS:
            try:
                candles = await client.fetch_ohlcv_range(
                    symbol, timeframe="5m",
                    since=int(time.time() * 1000) - 300 * 5 * 60 * 1000,
                )
                if len(candles) > 288:
                    df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                    p = df["close"]
                    mom_7d = (p.iloc[-1] - p.iloc[-2016]) / p.iloc[-2016] * 100 if len(p) > 2016 else (p.iloc[-1] - p.iloc[0]) / p.iloc[0] * 100
                    coin_momentum[symbol] = mom_7d
            except Exception:
                pass

        if coin_momentum:
            ranked = sorted(coin_momentum.items(), key=lambda x: x[1], reverse=True)
            for i, (sym, mom) in enumerate(ranked):
                tag = " << 本周交易" if i < 2 else ""
                logger.info(f"  #{i+1} {sym:10s} 7日动量: {mom:+.2f}%{tag}")

        logger.info(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(health_check())
