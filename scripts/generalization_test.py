"""泛化测试：用 BTC/ETH 上找到的最优参数，在 SOL/DOGE/XRP 上验证。"""

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

from src.strategies.trend_ma_filtered import TrendMAFilteredStrategy
from src.strategies.aggressive_momentum import AggressiveMomentumStrategy
from src.strategies.rsi_extreme import RSIExtremeStrategy
from src.strategies.ensemble import EnsembleStrategy


def load_price(symbol: str, timeframe: str) -> pd.Series | None:
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, timeframe)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


# 最优参数（从 BTC/ETH 优化得到）
STRATEGIES = {
    "TrendMA_Filtered": {
        "cls": TrendMAFilteredStrategy,
        "params": {"short_window": 25, "long_window": 200, "atr_mult": 0.5},
    },
    "AggressiveMom": {
        "cls": AggressiveMomentumStrategy,
        "params": {"lookback": 50, "consec_bars": 4, "trail_atr_mult": 1.5},
    },
    "RSIExtreme": {
        "cls": RSIExtremeStrategy,
        "params": {"rsi_period": 14, "oversold": 25, "overbought": 75, "trend_ma": 200},
    },
    "Ensemble": {
        "cls": EnsembleStrategy,
        "params": {"min_agree": 2},
    },
}

SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT"]


def main() -> None:
    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="4h")

    all_rows = []

    for symbol in SYMBOLS:
        price = load_price(symbol, "4h")
        if price is None or len(price) < 200:
            logger.warning(f"跳过 {symbol}: 数据不足")
            continue

        for strat_name, config in STRATEGIES.items():
            strategy = config["cls"]()
            entries, exits = strategy.generate_signals(price, **config["params"])
            portfolio = engine.run(price, entries, exits)
            metrics = compute_metrics(portfolio)

            row = {
                "symbol": symbol,
                "strategy": strat_name,
                **metrics,
            }
            all_rows.append(row)

    df = pd.DataFrame(all_rows)

    # 打印结果
    logger.info(f"\n{'='*80}")
    logger.info("泛化测试结果 | Top 4 策略 x 5 币种 | 4h")
    logger.info(f"{'='*80}")

    for strat_name in STRATEGIES:
        subset = df[df["strategy"] == strat_name].sort_values("sharpe_ratio", ascending=False)
        logger.info(f"\n  --- {strat_name} ---")
        for _, r in subset.iterrows():
            logger.info(
                f"    {r['symbol']:12s} | "
                f"sharpe:{r['sharpe_ratio']:+.3f} | "
                f"ret:{r['total_return_pct']:+8.2f}% | "
                f"dd:{r['max_drawdown_pct']:5.1f}% | "
                f"trades:{int(r['total_trades']):3d}"
            )

    # 按策略平均
    logger.info(f"\n{'='*80}")
    logger.info("策略平均表现（跨 5 币种）")
    logger.info(f"{'='*80}")
    avg = df.groupby("strategy").agg({
        "sharpe_ratio": "mean",
        "total_return_pct": "mean",
        "max_drawdown_pct": "mean",
        "total_trades": "mean",
    }).sort_values("sharpe_ratio", ascending=False)

    for strat, r in avg.iterrows():
        logger.info(
            f"  {strat:20s} | "
            f"avg_sharpe:{r['sharpe_ratio']:+.3f} | "
            f"avg_ret:{r['total_return_pct']:+7.2f}% | "
            f"avg_dd:{r['max_drawdown_pct']:5.1f}% | "
            f"avg_trades:{r['total_trades']:5.1f}"
        )

    # 保存
    output = Path("reports") / "generalization_test.csv"
    df.to_csv(output, index=False)
    logger.info(f"\n保存到: {output}")
    logger.success("泛化测试完成！")


if __name__ == "__main__":
    main()
