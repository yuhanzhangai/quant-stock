"""实时信号生成器：每 5 分钟检查一次 MinSwing 信号。

这不是自动交易！只生成信号提醒你：
- 哪个币种出现了入场信号
- 建议入场价、止损价、止盈价
- 当前市场状态

运行方式：python scripts/live_signal.py
"""

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.exchange.whale_detector import WhaleDetector
from src.exchange.news_sentiment import get_market_sentiment
from src.strategies.minute_swing import MinuteSwingStrategy

# 监控的币种和最优参数
COINS = {
    "ETH/USDT": {"trend_ma": 180, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 144},
    "SOL/USDT": {"trend_ma": 180, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 144},
    "NEAR/USDT": {"trend_ma": 180, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 144},
    "ARB/USDT": {"trend_ma": 180, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 144},
}

LEVERAGE = 5
CAPITAL = 50.0
PER_COIN = CAPITAL / len(COINS)


async def check_signals() -> None:
    """拉取最新数据，检查信号。"""
    settings = get_settings()
    strat = MinuteSwingStrategy()

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        now = datetime.now(timezone.utc)
        logger.info(f"\n{'='*60}")
        logger.info(f"信号扫描 | {now.strftime('%Y-%m-%d %H:%M UTC')} | ${CAPITAL} x {LEVERAGE}x")
        logger.info(f"{'='*60}")

        for symbol, params in COINS.items():
            try:
                # 拉最近 300 根 5m K 线
                candles = await client.fetch_ohlcv_range(
                    symbol, timeframe="5m",
                    since=int(time.time() * 1000) - 300 * 5 * 60 * 1000,
                )

                if len(candles) < 200:
                    logger.warning(f"{symbol}: 数据不足 ({len(candles)} bars)")
                    continue

                import pandas as pd

                df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                price = df.set_index("datetime")["close"]

                # 生成信号
                entries, exits = strat.generate_signals(price, **params)

                current_price = price.iloc[-1]
                last_entry = entries[entries].index[-1] if entries.any() else None
                last_exit = exits[exits].index[-1] if exits.any() else None

                # 检查最后 3 根是否有新信号
                recent_entry = entries.iloc[-3:].any()
                recent_exit = exits.iloc[-3:].any()

                # 计算关键价位
                stop_price = current_price * (1 - params["stop_pct"] / 100)
                tp_price = current_price * (1 + params["take_profit_pct"] / 100)
                position_size = PER_COIN * LEVERAGE

                # 趋势状态
                ma = price.rolling(window=params["trend_ma"]).mean()
                trend = "UP" if current_price > ma.iloc[-1] else "DOWN"

                # RSI
                delta = price.diff()
                gains = delta.clip(lower=0).rolling(window=14).mean()
                losses = (-delta).clip(lower=0).rolling(window=14).mean()
                rs = gains / losses
                rsi = (100 - (100 / (1 + rs))).iloc[-1]

                status = ""
                if recent_entry:
                    status = ">>> ENTRY SIGNAL <<<"
                elif recent_exit:
                    status = ">>> EXIT SIGNAL <<<"
                else:
                    status = "waiting"

                logger.info(
                    f"\n  {symbol:10s} | price: ${current_price:,.2f} | trend: {trend} | RSI: {rsi:.0f}"
                )
                if recent_entry:
                    logger.info(
                        f"  {'':10s} | ENTRY! buy @ ${current_price:,.2f}"
                    )
                    logger.info(
                        f"  {'':10s} | SL: ${stop_price:,.2f} ({params['stop_pct']}%) | "
                        f"TP: ${tp_price:,.2f} ({params['take_profit_pct']}%)"
                    )
                    logger.info(
                        f"  {'':10s} | position: ${position_size:,.2f} ({LEVERAGE}x)"
                    )
                elif recent_exit:
                    logger.info(f"  {'':10s} | EXIT! close position")
                else:
                    logger.info(f"  {'':10s} | {status}")

            except Exception as e:
                logger.error(f"{symbol}: {e}")

        # 辅助信息（不影响信号，仅供参考）
        logger.info(f"\n  --- 辅助信息 ---")

        # 鲸鱼检测
        whale = WhaleDetector(threshold_usd=10000)
        for sym_okx in ["BTC-USDT", "ETH-USDT"]:
            try:
                wr = await whale.scan(sym_okx)
                if wr["whale_count"] > 0:
                    logger.info(
                        f"  鲸鱼 {sym_okx}: {wr['whale_count']} 大单 "
                        f"买${wr['whale_buy_volume']:,.0f} "
                        f"卖${wr['whale_sell_volume']:,.0f} → {wr['net_direction']}"
                    )
            except Exception:
                pass

        # 新闻情绪（辅助参考）
        try:
            sentiment = await get_market_sentiment()
            if sentiment["available"]:
                logger.info(f"  新闻情绪: {sentiment['sentiment']} ({sentiment['news_count']} 条)")
            else:
                logger.info(f"  新闻情绪: 不可用（正常，不影响策略）")
        except Exception:
            pass


async def main() -> None:
    logger.info("MinSwing 实时信号监控启动")
    logger.info(f"监控币种: {list(COINS.keys())}")
    logger.info(f"资金: ${CAPITAL} x {LEVERAGE}x 杠杆")
    logger.info("每 5 分钟扫描一次 (Ctrl+C 停止)\n")

    while True:
        await check_signals()
        logger.info("\n下次扫描: 5 分钟后...")
        await asyncio.sleep(300)


if __name__ == "__main__":
    # 单次运行
    if "--once" in sys.argv:
        asyncio.run(check_signals())
    else:
        asyncio.run(main())
