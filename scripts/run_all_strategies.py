"""多策略回测：跑所有策略并比较。

⚠️ PAUSED (2026-04-23)
当前阶段不继续扩张策略数量。
只有在完成 validation pipeline (Checkpoint 7) 后，才允许重新启用。
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.backtest.reports import generate_report
from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings

from src.strategies.trend_ma import trend_ma_signal_func
from src.strategies.trend_ma_filtered import trend_ma_filtered_signal
from src.strategies.momentum_breakout import momentum_breakout_signal
from src.strategies.mean_reversion_bb import mean_reversion_bb_signal


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


def run_strategy_suite(symbol: str, timeframe: str) -> None:
    price = load_price(symbol, timeframe)
    if price is None or len(price) < 200:
        logger.warning(f"数据不足: {symbol} {timeframe}")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"策略对比 | {symbol} {timeframe} | {len(price)} bars")
    logger.info(f"{'='*60}")

    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="1h" if "h" in timeframe else "1d")

    all_results = []

    # === 1. 原始双均线 ===
    logger.info("\n--- 1. TrendMA (原始) ---")
    grid1 = {"short_window": [10, 20, 30], "long_window": [50, 100, 200]}
    df1, best1 = engine.run_grid_search(price, trend_ma_signal_func, grid1)
    df1["strategy"] = "TrendMA"
    all_results.append(df1)

    # === 2. 带过滤的双均线 ===
    logger.info("\n--- 2. TrendMA_Filtered (ATR+RSI) ---")
    grid2 = {"short_window": [10, 20, 30], "long_window": [50, 100, 200], "atr_mult": [0.3, 0.5, 1.0]}
    df2, best2 = engine.run_grid_search(price, trend_ma_filtered_signal, grid2)
    df2["strategy"] = "TrendMA_Filtered"
    all_results.append(df2)

    # === 3. 动量突破 ===
    logger.info("\n--- 3. Momentum Breakout (Donchian) ---")
    grid3 = {"entry_window": [20, 50, 100], "exit_window": [10, 20, 30]}
    df3, best3 = engine.run_grid_search(price, momentum_breakout_signal, grid3)
    df3["strategy"] = "MomentumBreakout"
    all_results.append(df3)

    # === 4. 均值回归 BB ===
    logger.info("\n--- 4. Mean Reversion BB ---")
    grid4 = {"bb_period": [15, 20, 30], "bb_std": [1.5, 2.0, 2.5]}
    df4, best4 = engine.run_grid_search(price, mean_reversion_bb_signal, grid4)
    df4["strategy"] = "MeanRevBB"
    all_results.append(df4)

    # === 汇总 ===
    combined = pd.concat(all_results, ignore_index=True)
    combined = combined.sort_values("sharpe_ratio", ascending=False)

    logger.info(f"\n{'='*60}")
    logger.info(f"全部结果排名 (按夏普)  | {symbol} {timeframe}")
    logger.info(f"{'='*60}")

    for i, row in combined.head(15).iterrows():
        logger.info(
            f"  [{row['strategy']:20s}] "
            f"收益: {row['total_return_pct']:+7.2f}% | "
            f"夏普: {row['sharpe_ratio']:+.3f} | "
            f"回撤: {row['max_drawdown_pct']:5.1f}% | "
            f"胜率: {row['win_rate_pct']:4.1f}% | "
            f"交易: {int(row['total_trades']):3d}"
        )

    # 最优策略生成报告
    best_row = combined.iloc[0]
    logger.info(f"\n最优: {best_row['strategy']} | 夏普: {best_row['sharpe_ratio']:.3f}")

    # 保存对比结果
    output_path = Path("reports") / f"comparison_{symbol}_{timeframe}.csv"
    output_path.parent.mkdir(exist_ok=True)
    combined.to_csv(output_path, index=False)
    logger.info(f"对比结果保存到: {output_path}")


def main() -> None:
    # 在多个品种和周期上测试
    combos = [
        ("BTC-USDT", "1h"),
        ("BTC-USDT", "4h"),
        ("BTC-USDT", "1d"),
        ("ETH-USDT", "1h"),
        ("ETH-USDT", "4h"),
        ("ETH-USDT", "1d"),
    ]

    for symbol, tf in combos:
        run_strategy_suite(symbol, tf)

    logger.success("\n全部策略回测完成！")


if __name__ == "__main__":
    main()
