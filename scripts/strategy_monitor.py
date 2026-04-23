"""策略健康监控：每天拉新数据验证 MinSwing 是否仍有效。

如果最近 2 周的 sharpe < 0，发出警告。
如果连续 5 笔亏损，发出严重警告。
"""

import asyncio
import sys
import time
from pathlib import Path

import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.strategies.minswing_v3_final import minswing_v3_signal


async def monitor() -> None:
    settings = get_settings()
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")

    logger.info(f"\n{'='*60}")
    logger.info(f"策略健康监控 | {time.strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"{'='*60}")

    coins = {"ETH/USDT": "ETH", "SOL/USDT": "SOL", "NEAR/USDT": "NEAR", "ARB/USDT": "ARB"}
    warnings = []

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        for symbol, coin in coins.items():
            try:
                # 拉最近 1 个月 5m 数据（2 周太短有噪音）
                candles = await client.fetch_ohlcv_range(
                    symbol, timeframe="5m",
                    since=int(time.time() * 1000) - 30 * 24 * 3600 * 1000,
                )
                if len(candles) < 500:
                    continue

                df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                price = df.set_index("datetime")["close"]

                # 跑回测
                e, x = minswing_v3_signal(price, coin=coin)
                pf = engine.run(price, e, x)
                m = compute_metrics(pf)

                sharpe = m["sharpe_ratio"]
                ret = m["total_return_pct"]
                trades = m["total_trades"]

                # 分析最近交易
                ei = e[e].index
                xi = x[x].index
                recent_results = []
                for entry in ei:
                    nx = xi[xi > entry]
                    if len(nx) == 0:
                        continue
                    exit_t = nx[0]
                    trade_ret = (price.loc[exit_t] - price.loc[entry]) / price.loc[entry] * 100
                    recent_results.append(trade_ret)

                consec_losses = 0
                for r in reversed(recent_results):
                    if r < 0:
                        consec_losses += 1
                    else:
                        break

                # 状态判断
                if sharpe > 1:
                    status = "HEALTHY"
                elif sharpe > 0:
                    status = "OK"
                elif sharpe > -1:
                    status = "WARNING"
                    warnings.append(f"{symbol}: sharpe {sharpe:.2f}")
                else:
                    status = "CRITICAL"
                    warnings.append(f"{symbol}: sharpe {sharpe:.2f} CRITICAL")

                if consec_losses >= 5:
                    status = "CRITICAL"
                    warnings.append(f"{symbol}: {consec_losses} consecutive losses!")

                logger.info(
                    f"\n  {symbol:10s} | sharpe: {sharpe:+.2f} | ret: {ret:+.1f}% | "
                    f"trades: {trades} | streak: {consec_losses} losses | {status}"
                )

            except Exception as ex:
                logger.error(f"  {symbol}: {ex}")

    # 总结
    logger.info(f"\n  --- Summary ---")
    if not warnings:
        logger.info(f"  All strategies HEALTHY. Continue trading.")
    else:
        for w in warnings:
            logger.warning(f"  {w}")
        logger.warning(f"  Consider pausing affected coins until recovery.")

    logger.info(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(monitor())
