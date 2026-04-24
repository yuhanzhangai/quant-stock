"""回测演示：双均线策略在 BTC-USDT 1h 上的回测 + 参数搜索。"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.backtest.reports import generate_report
from src.data_quality.checks import has_critical_failure, run_all_checks
from src.strategies.trend_ma import TrendMAStrategy, trend_ma_signal_func
from src.storage.parquet_writer import ParquetWriter


def load_price_data(symbol: str, timeframe: str) -> pd.Series:
    """从 Parquet 加载价格数据。"""
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, timeframe)

    if df.is_empty():
        logger.warning(f"无数据: {symbol} {timeframe}，返回 None")
        return None

    # Data quality gate
    logger.info(f"Running data quality checks for {symbol}/{timeframe}...")
    results = run_all_checks(df, timeframe=timeframe)
    if has_critical_failure(results):
        raise RuntimeError(f"Data quality gate failed for {symbol}/{timeframe}")
    logger.info("Data quality: PASS")

    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    pdf = pdf.set_index("datetime").sort_index()

    logger.info(f"加载数据 | {symbol} {timeframe} | {len(pdf)} 行 | {pdf.index[0]} ~ {pdf.index[-1]}")
    return pdf["close"]


def get_demo_price() -> pd.Series:
    """生成模拟价格数据用于演示。"""
    import numpy as np
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=2000, freq="1h", tz="UTC")
    returns = np.random.normal(0.0001, 0.01, 2000)
    return pd.Series(42000 * np.exp(np.cumsum(returns)), index=dates, name="close")


def main() -> None:
    logger.info("=" * 60)
    logger.info("回测演示：双均线策略")
    logger.info("=" * 60)

    # 加载数据
    price = load_price_data("BTC-USDT", "1h")

    if price is None or len(price) < 100:
        logger.warning("数据不足，使用模拟数据演示")
        price = get_demo_price()

    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="1h")

    # 1. 单次回测
    logger.info("\n--- 单次回测 (short=10, long=50) ---")
    strategy = TrendMAStrategy()
    entries, exits = strategy.generate_signals(price, short_window=10, long_window=50)
    portfolio = engine.run(price, entries, exits)

    metrics = compute_metrics(portfolio)
    for k, v in metrics.items():
        logger.info(f"  {k}: {v}")

    # 生成报告
    report_path = generate_report(portfolio, title="BTC-USDT TrendMA 10-50")
    logger.info(f"报告: {report_path}")

    # 2. 参数搜索
    logger.info("\n--- 参数网格搜索 ---")
    param_grid = {
        "short_window": [5, 10, 20],
        "long_window": [50, 100, 200],
    }

    results_df, best_params = engine.run_grid_search(
        price, trend_ma_signal_func, param_grid
    )

    logger.info(f"\n最优参数: {best_params}")
    logger.info("\n全部结果:")
    for _, row in results_df.iterrows():
        logger.info(
            f"  short={int(row['short_window']):3d} long={int(row['long_window']):3d} | "
            f"收益: {row['total_return_pct']:+.2f}% | "
            f"夏普: {row['sharpe_ratio']:.3f} | "
            f"回撤: {row['max_drawdown_pct']:.2f}% | "
            f"交易: {int(row['total_trades'])}"
        )

    # 用最优参数跑一次并生成报告
    best_entries, best_exits = trend_ma_signal_func(price, **best_params)
    best_portfolio = engine.run(price, best_entries, best_exits)
    generate_report(
        best_portfolio,
        title=f"BTC-USDT TrendMA Best {best_params['short_window']}-{best_params['long_window']}",
    )

    logger.success("回测完成！")


if __name__ == "__main__":
    main()
