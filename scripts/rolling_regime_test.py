"""滚动时间窗口验证：每个策略在不同市场时期的表现。

把 2 年数据分成 4 个半年窗口，看哪些策略在哪些时期好/差。
不同时期交易情绪不同，某些策略在特定时期可能翻身。
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings

from src.strategies.trend_ma_filtered import trend_ma_filtered_signal
from src.strategies.aggressive_momentum import aggressive_momentum_signal
from src.strategies.rsi_extreme import rsi_extreme_signal
from src.strategies.ensemble import ensemble_signal
from src.strategies.macd_histogram import macd_histogram_signal
from src.strategies.momentum_breakout import momentum_breakout_signal
from src.strategies.mean_reversion_bb import mean_reversion_bb_signal
from src.strategies.ichimoku import ichimoku_signal
from src.strategies.keltner_breakout import keltner_signal
from src.strategies.turtle_trading import turtle_signal
from src.strategies.supertrend import supertrend_signal
from src.strategies.multi_timeframe import multi_timeframe_signal
from src.strategies.adaptive import adaptive_signal

ALL_STRATEGIES = {
    "TrendMA_Filt": (trend_ma_filtered_signal, {"short_window": 25, "long_window": 200, "atr_mult": 0.5}),
    "AggressiveMom": (aggressive_momentum_signal, {"lookback": 50, "consec_bars": 4, "trail_atr_mult": 1.5}),
    "RSIExtreme": (rsi_extreme_signal, {"rsi_period": 14, "oversold": 25, "overbought": 75, "trend_ma": 200}),
    "Ensemble": (ensemble_signal, {"min_agree": 2}),
    "MACD_Hist": (macd_histogram_signal, {"fast": 12, "slow": 26, "signal": 9, "trend_ma": 200}),
    "MomBreakout": (momentum_breakout_signal, {"entry_window": 50, "exit_window": 20}),
    "MeanRevBB": (mean_reversion_bb_signal, {"bb_period": 20, "bb_std": 2.0}),
    "Ichimoku": (ichimoku_signal, {"tenkan": 9, "kijun": 26, "senkou_b": 52}),
    "Keltner": (keltner_signal, {"ema_period": 20, "atr_period": 14, "atr_mult": 2.0}),
    "Turtle": (turtle_signal, {"entry_period": 20, "exit_period": 10, "atr_stop_mult": 2.0}),
    "SuperTrend": (supertrend_signal, {"atr_period": 14, "multiplier": 3.0, "adx_threshold": 20}),
    "MultiTF": (multi_timeframe_signal, {"daily_ma": 150, "short_ma": 10, "long_ma": 40}),
    "Adaptive": (adaptive_signal, {"short_ma": 20, "long_ma": 100, "bb_period": 20}),
}


def load_price(symbol: str, tf: str) -> pd.Series | None:
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, tf)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


def main() -> None:
    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="4h")
    all_rows = []

    for symbol in ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT"]:
        price = load_price(symbol, "4h")
        if price is None or len(price) < 500:
            continue

        # 分成 4 个半年窗口
        total = len(price)
        window = total // 4

        for wi in range(4):
            start = wi * window
            end = min(start + window, total)
            chunk = price.iloc[start:end]
            period_label = f"{chunk.index[0].strftime('%Y-%m')} ~ {chunk.index[-1].strftime('%Y-%m')}"

            if len(chunk) < 100:
                continue

            for strat_name, (func, params) in ALL_STRATEGIES.items():
                try:
                    e, x = func(chunk, **params)
                    pf = engine.run(chunk, e, x)
                    m = compute_metrics(pf)
                    all_rows.append({
                        "symbol": symbol,
                        "period": period_label,
                        "window": wi + 1,
                        "strategy": strat_name,
                        **m,
                    })
                except Exception:
                    all_rows.append({
                        "symbol": symbol, "period": period_label, "window": wi + 1,
                        "strategy": strat_name, "sharpe_ratio": 0, "total_return_pct": 0,
                        "max_drawdown_pct": 0, "total_trades": 0,
                    })

    df = pd.DataFrame(all_rows)

    # 分析
    logger.info(f"\n{'='*90}")
    logger.info("滚动时间窗口验证 | 13 策略 x 5 币种 x 4 半年窗口")
    logger.info(f"{'='*90}")

    # 每个窗口的最优策略
    for wi in range(1, 5):
        chunk = df[df["window"] == wi]
        if chunk.empty:
            continue
        best = chunk.nlargest(5, "sharpe_ratio")
        period = best.iloc[0].get("period", f"W{wi}")
        logger.info(f"\n  --- 窗口 {wi}: {period} ---")
        for _, r in best.iterrows():
            logger.info(
                f"    {r['symbol']:12s} {r['strategy']:15s} | "
                f"sharpe:{r['sharpe_ratio']:+.3f} | ret:{r['total_return_pct']:+7.2f}% | "
                f"dd:{r['max_drawdown_pct']:5.1f}%"
            )

    # 每个策略的稳定性（跨窗口标准差）
    logger.info(f"\n{'='*90}")
    logger.info("策略稳定性（跨所有窗口+币种）")
    logger.info(f"{'='*90}")
    stability = df.groupby("strategy").agg(
        avg_sharpe=("sharpe_ratio", "mean"),
        std_sharpe=("sharpe_ratio", "std"),
        pct_positive=("sharpe_ratio", lambda x: (x > 0).mean() * 100),
        avg_ret=("total_return_pct", "mean"),
    ).sort_values("avg_sharpe", ascending=False)

    for strat, r in stability.iterrows():
        logger.info(
            f"  {strat:15s} | avg_sharpe:{r['avg_sharpe']:+.3f} | "
            f"std:{r['std_sharpe']:.3f} | "
            f"positive:{r['pct_positive']:4.0f}% | "
            f"avg_ret:{r['avg_ret']:+6.2f}%"
        )

    # 保存
    output = Path("reports") / "rolling_regime_test.csv"
    df.to_csv(output, index=False)
    logger.info(f"\n保存到: {output}")
    logger.success("滚动验证完成！")


if __name__ == "__main__":
    main()
