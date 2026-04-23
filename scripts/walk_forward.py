"""Walk-Forward 验证：防止过拟合。

将数据分成多个窗口，在训练集优化参数，在测试集验证。
如果测试集表现远差于训练集，说明过拟合。
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
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
from src.strategies.macd_histogram import macd_histogram_signal
from src.strategies.ensemble import ensemble_signal


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


STRATEGIES = {
    "TrendMA_Filtered": {
        "func": trend_ma_filtered_signal,
        "params": {"short_window": 25, "long_window": 200, "atr_mult": 0.5},
    },
    "AggressiveMom": {
        "func": aggressive_momentum_signal,
        "params": {"lookback": 50, "consec_bars": 4, "trail_atr_mult": 1.5},
    },
    "RSIExtreme": {
        "func": rsi_extreme_signal,
        "params": {"rsi_period": 14, "oversold": 25, "overbought": 75, "trend_ma": 200},
    },
    "MACD_Histogram": {
        "func": macd_histogram_signal,
        "params": {"fast": 12, "slow": 26, "signal": 9, "trend_ma": 200},
    },
    "Ensemble": {
        "func": ensemble_signal,
        "params": {"min_agree": 2},
    },
}


def walk_forward_test(
    price: pd.Series,
    strategy_name: str,
    n_splits: int = 4,
    train_ratio: float = 0.7,
) -> dict:
    """对单个策略做 Walk-Forward 验证。

    将数据分成 n_splits 个窗口，每个窗口前 70% 训练，后 30% 测试。
    """
    strat = STRATEGIES[strategy_name]
    total_len = len(price)
    window_size = total_len // n_splits

    train_sharpes = []
    test_sharpes = []
    test_returns = []

    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="4h")

    for i in range(n_splits):
        start = i * window_size
        end = min(start + window_size, total_len)
        window = price.iloc[start:end]

        split_point = int(len(window) * train_ratio)
        train_data = window.iloc[:split_point]
        test_data = window.iloc[split_point:]

        if len(train_data) < 100 or len(test_data) < 50:
            continue

        # 训练集
        try:
            e_train, x_train = strat["func"](train_data, **strat["params"])
            pf_train = engine.run(train_data, e_train, x_train)
            m_train = compute_metrics(pf_train)
            train_sharpes.append(m_train["sharpe_ratio"])
        except Exception:
            train_sharpes.append(0.0)

        # 测试集
        try:
            e_test, x_test = strat["func"](test_data, **strat["params"])
            pf_test = engine.run(test_data, e_test, x_test)
            m_test = compute_metrics(pf_test)
            test_sharpes.append(m_test["sharpe_ratio"])
            test_returns.append(m_test["total_return_pct"])
        except Exception:
            test_sharpes.append(0.0)
            test_returns.append(0.0)

    avg_train_sharpe = np.mean(train_sharpes) if train_sharpes else 0
    avg_test_sharpe = np.mean(test_sharpes) if test_sharpes else 0
    avg_test_return = np.mean(test_returns) if test_returns else 0
    degradation = (avg_train_sharpe - avg_test_sharpe) / max(abs(avg_train_sharpe), 0.01)

    return {
        "strategy": strategy_name,
        "avg_train_sharpe": round(avg_train_sharpe, 3),
        "avg_test_sharpe": round(avg_test_sharpe, 3),
        "avg_test_return_pct": round(avg_test_return, 2),
        "degradation_pct": round(degradation * 100, 1),
        "n_windows": len(test_sharpes),
        "overfit_risk": "HIGH" if degradation > 0.5 else "MEDIUM" if degradation > 0.2 else "LOW",
    }


def main() -> None:
    for symbol in ["BTC-USDT", "ETH-USDT"]:
        price = load_price(symbol, "4h")
        if price is None:
            continue

        logger.info(f"\n{'='*70}")
        logger.info(f"Walk-Forward 验证 | {symbol} 4h | {len(price)} bars | 4 窗口")
        logger.info(f"{'='*70}")

        results = []
        for name in STRATEGIES:
            r = walk_forward_test(price, name, n_splits=4)
            results.append(r)
            logger.info(
                f"  {r['strategy']:20s} | "
                f"train_sharpe: {r['avg_train_sharpe']:+.3f} | "
                f"test_sharpe: {r['avg_test_sharpe']:+.3f} | "
                f"test_ret: {r['avg_test_return_pct']:+.2f}% | "
                f"degradation: {r['degradation_pct']:+.1f}% | "
                f"overfit: {r['overfit_risk']}"
            )

        # 保存
        df = pd.DataFrame(results)
        output = Path("reports") / f"walk_forward_{symbol}.csv"
        df.to_csv(output, index=False)
        logger.info(f"保存到: {output}")

    logger.success("\nWalk-Forward 验证完成！")


if __name__ == "__main__":
    main()
