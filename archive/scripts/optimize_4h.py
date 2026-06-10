"""聚焦 4h 周期深度优化所有策略。"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategies.adaptive import adaptive_signal
from src.strategies.aggressive_momentum import aggressive_momentum_signal, multi_factor_signal
from src.strategies.ensemble import ensemble_signal
from src.strategies.ichimoku import ichimoku_signal
from src.strategies.keltner_breakout import keltner_signal
from src.strategies.macd_histogram import macd_histogram_signal
from src.strategies.mean_reversion_bb import mean_reversion_bb_signal
from src.strategies.momentum_breakout import momentum_breakout_signal
from src.strategies.momentum_mean_blend import momentum_mean_blend_signal
from src.strategies.rsi_extreme import rsi_extreme_signal
from src.strategies.trend_ma_filtered import trend_ma_filtered_signal
from src.strategies.turtle_trading import turtle_signal

from config.settings import get_settings
from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.storage.parquet_writer import ParquetWriter


def load_price(symbol: str, timeframe: str) -> pd.Series | None:
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, timeframe)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    pdf = pdf.set_index("datetime").sort_index()
    return pdf["close"]


def optimize_symbol(symbol: str) -> None:
    price = load_price(symbol, "4h")
    if price is None or len(price) < 200:
        logger.warning(f"数据不足: {symbol} 4h")
        return

    logger.info(f"\n{'=' * 70}")
    logger.info(f"4h 深度优化 | {symbol} | {len(price)} bars")
    logger.info(f"{'=' * 70}")

    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="4h")
    all_results = []

    # 1. TrendMA_Filtered 细粒度参数
    logger.info("\n--- TrendMA_Filtered 细粒度搜索 ---")
    grid1 = {
        "short_window": [15, 20, 25, 30, 40],
        "long_window": [80, 100, 120, 150, 200],
        "atr_mult": [0.3, 0.5, 0.8, 1.0, 1.5],
    }
    df1, best1 = engine.run_grid_search(price, trend_ma_filtered_signal, grid1)
    df1["strategy"] = "TrendMA_Filtered"
    all_results.append(df1)

    # 2. Momentum Breakout 细粒度
    logger.info("\n--- Momentum Breakout 细粒度搜索 ---")
    grid2 = {
        "entry_window": [30, 50, 70, 100, 150],
        "exit_window": [10, 15, 20, 30, 50],
    }
    df2, best2 = engine.run_grid_search(price, momentum_breakout_signal, grid2)
    df2["strategy"] = "MomentumBreakout"
    all_results.append(df2)

    # 3. MeanRevBB (with trend filter)
    logger.info("\n--- MeanRevBB (趋势过滤版) ---")
    grid3 = {
        "bb_period": [15, 20, 30, 40],
        "bb_std": [1.5, 2.0, 2.5, 3.0],
    }
    df3, best3 = engine.run_grid_search(price, mean_reversion_bb_signal, grid3)
    df3["strategy"] = "MeanRevBB_TF"
    all_results.append(df3)

    # 4. RSI Extreme
    logger.info("\n--- RSI Extreme ---")
    grid4 = {
        "rsi_period": [10, 14, 20],
        "oversold": [15, 20, 25, 30],
        "overbought": [70, 75, 80, 85],
        "trend_ma": [100, 200],
    }
    df4, best4 = engine.run_grid_search(price, rsi_extreme_signal, grid4)
    df4["strategy"] = "RSIExtreme"
    all_results.append(df4)

    # 5. Aggressive Momentum (追涨)
    logger.info("\n--- Aggressive Momentum ---")
    grid5 = {
        "lookback": [10, 20, 30, 50],
        "consec_bars": [2, 3, 4],
        "trail_atr_mult": [1.5, 2.0, 3.0],
    }
    df5, best5 = engine.run_grid_search(price, aggressive_momentum_signal, grid5)
    df5["strategy"] = "AggressiveMom"
    all_results.append(df5)

    # 6. Multi-Factor Aggressive
    logger.info("\n--- Multi-Factor Aggressive ---")
    grid6 = {
        "ma_period": [100, 150, 200],
        "mom_period": [5, 10, 20],
        "min_votes": [2, 3, 4],
    }
    df6, best6 = engine.run_grid_search(price, multi_factor_signal, grid6)
    df6["strategy"] = "MultiFactor"
    all_results.append(df6)

    # 7. Keltner Channel Breakout
    logger.info("\n--- Keltner Channel Breakout ---")
    grid7 = {
        "ema_period": [15, 20, 30],
        "atr_period": [10, 14, 20],
        "atr_mult": [1.5, 2.0, 2.5, 3.0],
    }
    df7, _ = engine.run_grid_search(price, keltner_signal, grid7)
    df7["strategy"] = "Keltner"
    all_results.append(df7)

    # 8. Turtle Trading
    logger.info("\n--- Turtle Trading ---")
    grid8 = {
        "entry_period": [20, 30, 55],
        "exit_period": [10, 15, 20],
        "atr_stop_mult": [1.5, 2.0, 3.0],
    }
    df8, _ = engine.run_grid_search(price, turtle_signal, grid8)
    df8["strategy"] = "Turtle"
    all_results.append(df8)

    # 9. Momentum + Mean Reversion Blend
    logger.info("\n--- Momentum Mean Blend ---")
    grid9 = {
        "ma_period": [30, 50, 100],
        "rsi_period": [10, 14, 20],
        "bb_period": [15, 20, 30],
    }
    df9, _ = engine.run_grid_search(price, momentum_mean_blend_signal, grid9)
    df9["strategy"] = "MomMeanBlend"
    all_results.append(df9)

    # 10. MACD Histogram
    logger.info("\n--- MACD Histogram ---")
    grid10 = {
        "fast": [8, 12, 16],
        "slow": [21, 26, 34],
        "signal": [7, 9, 12],
        "trend_ma": [100, 200],
    }
    df10, _ = engine.run_grid_search(price, macd_histogram_signal, grid10)
    df10["strategy"] = "MACD_Hist"
    all_results.append(df10)

    # 11. Ichimoku Cloud
    logger.info("\n--- Ichimoku Cloud ---")
    grid11 = {
        "tenkan": [7, 9, 12],
        "kijun": [22, 26, 30],
        "senkou_b": [44, 52, 60],
    }
    df11, _ = engine.run_grid_search(price, ichimoku_signal, grid11)
    df11["strategy"] = "Ichimoku"
    all_results.append(df11)

    # 12. Ensemble
    logger.info("\n--- Ensemble (Top 3 voting) ---")
    grid12 = {
        "min_agree": [1, 2],
        "tf_short": [20, 25, 30],
        "tf_long": [150, 200],
    }
    df12, _ = engine.run_grid_search(price, ensemble_signal, grid12)
    df12["strategy"] = "Ensemble"
    all_results.append(df12)

    # 13. Adaptive (regime-switching)
    logger.info("\n--- Adaptive (regime-switching) ---")
    grid13 = {
        "short_ma": [15, 20, 30],
        "long_ma": [80, 100, 150],
        "bb_period": [15, 20, 30],
    }
    df13, _ = engine.run_grid_search(price, adaptive_signal, grid13)
    df13["strategy"] = "Adaptive"
    all_results.append(df13)

    # 汇总
    combined = pd.concat(all_results, ignore_index=True)
    combined = combined[combined["total_trades"] > 0]
    combined = combined.sort_values("sharpe_ratio", ascending=False)

    logger.info(f"\n{'=' * 70}")
    logger.info(f"4h Top 15 | {symbol}")
    logger.info(f"{'=' * 70}")

    for _, row in combined.head(15).iterrows():
        # 收集参数信息
        params = {
            k: row[k]
            for k in row.index
            if k
            not in [
                "strategy",
                "total_return",
                "total_return_pct",
                "final_value",
                "sharpe_ratio",
                "sortino_ratio",
                "max_drawdown_pct",
                "win_rate_pct",
                "total_trades",
                "init_cash",
            ]
        }
        param_str = " ".join(f"{k}={v}" for k, v in params.items() if pd.notna(v))
        logger.info(
            f"  [{row['strategy']:18s}] "
            f"ret:{row['total_return_pct']:+8.2f}% | "
            f"sharpe:{row['sharpe_ratio']:+.3f} | "
            f"dd:{row['max_drawdown_pct']:5.1f}% | "
            f"wr:{row['win_rate_pct']:4.1f}% | "
            f"trades:{int(row['total_trades']):3d} | "
            f"{param_str}"
        )

    # 保存
    output_path = Path("reports") / f"optimize_4h_{symbol}.csv"
    combined.to_csv(output_path, index=False)
    logger.info(f"保存到: {output_path}")

    # 最优参数生成报告
    if not combined.empty:
        best_row = combined.iloc[0]
        logger.info(f"\n最优: {best_row['strategy']} sharpe={best_row['sharpe_ratio']:.3f}")


def main() -> None:
    for symbol in ["BTC-USDT", "ETH-USDT"]:
        optimize_symbol(symbol)
    logger.success("\n4h 深度优化完成！")


if __name__ == "__main__":
    main()
