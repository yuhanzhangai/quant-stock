"""样本外验证：前 1 年训练参数，后 1 年纯测试。

避免在同一段数据上反复优化导致过拟合幻觉。
训练期：2024-04 ~ 2025-04
测试期：2025-04 ~ 2026-04（完全不同的市场环境）
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategies.aggressive_momentum import aggressive_momentum_signal
from src.strategies.ichimoku import ichimoku_signal
from src.strategies.ichimoku_momentum import ichimoku_momentum_signal
from src.strategies.macd_histogram import macd_histogram_signal
from src.strategies.momentum_breakout import momentum_breakout_signal
from src.strategies.trend_ma_filtered import trend_ma_filtered_signal

from config.settings import get_settings
from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter

STRATEGIES = {
    "IchiMom_v2": (ichimoku_momentum_signal, {"tenkan": 9, "kijun": 26, "lookback": 50, "consec_bars": 4}),
    "IchiMom_v1": (ichimoku_momentum_signal, {"tenkan": 9, "kijun": 26, "lookback": 30, "consec_bars": 3}),
    "AggrMom": (aggressive_momentum_signal, {"lookback": 50, "consec_bars": 4, "trail_atr_mult": 1.5}),
    "Ichimoku": (ichimoku_signal, {"tenkan": 9, "kijun": 26, "senkou_b": 52}),
    "MACD_Hist": (macd_histogram_signal, {"fast": 12, "slow": 26, "signal": 9, "trend_ma": 200}),
    "MomBreakout": (momentum_breakout_signal, {"entry_window": 50, "exit_window": 20}),
    "TrendMA_Filt": (trend_ma_filtered_signal, {"short_window": 25, "long_window": 200, "atr_mult": 0.5}),
}

# 主流币（不含 DOGE 等 meme）
COINS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "LINK-USDT", "ADA-USDT", "AVAX-USDT"]


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

    for sym in COINS:
        price = load_price(sym, "4h")
        if price is None or len(price) < 500:
            continue

        # 严格 50/50 分割：前半训练，后半测试
        mid = len(price) // 2
        train_price = price.iloc[:mid]
        test_price = price.iloc[mid:]

        train_period = f"{train_price.index[0].strftime('%Y-%m')} ~ {train_price.index[-1].strftime('%Y-%m')}"
        test_period = f"{test_price.index[0].strftime('%Y-%m')} ~ {test_price.index[-1].strftime('%Y-%m')}"

        for sname, (func, params) in STRATEGIES.items():
            for period_name, data in [("TRAIN", train_price), ("TEST", test_price)]:
                try:
                    e, x = func(data, **params)
                    pf = engine.run(data, e, x)
                    m = compute_metrics(pf)
                except Exception:
                    m = {"sharpe_ratio": 0, "total_return_pct": 0, "max_drawdown_pct": 0, "total_trades": 0}

                all_rows.append(
                    {
                        "symbol": sym,
                        "strategy": sname,
                        "period": period_name,
                        "period_dates": train_period if period_name == "TRAIN" else test_period,
                        **m,
                    }
                )

    df = pd.DataFrame(all_rows)

    # 分析
    logger.info(f"\n{'=' * 80}")
    logger.info("样本外验证 | 前半训练 vs 后半测试 | 7 主流币 x 7 策略")
    logger.info(f"{'=' * 80}")

    for sname in STRATEGIES:
        train = df[(df["strategy"] == sname) & (df["period"] == "TRAIN")]
        test = df[(df["strategy"] == sname) & (df["period"] == "TEST")]

        train_sharpe = train["sharpe_ratio"].mean()
        test_sharpe = test["sharpe_ratio"].mean()
        test_pos = (test["sharpe_ratio"] > 0).sum()
        test_total = len(test)
        degrad = train_sharpe - test_sharpe

        logger.info(
            f"  {sname:15s} | "
            f"train:{train_sharpe:+.3f} | "
            f"TEST:{test_sharpe:+.3f} | "
            f"degrad:{degrad:+.3f} | "
            f"test_pos:{test_pos}/{test_total} | "
            f"{'ROBUST' if degrad < 0.3 else 'OVERFIT' if degrad > 0.5 else 'OK'}"
        )

    # 每个币种的测试期最优
    logger.info(f"\n{'=' * 80}")
    logger.info("每个币种测试期最优策略")
    logger.info(f"{'=' * 80}")

    test_df = df[df["period"] == "TEST"]
    for sym in COINS:
        coin_test = test_df[test_df["symbol"] == sym]
        if coin_test.empty:
            continue
        best = coin_test.nlargest(1, "sharpe_ratio").iloc[0]
        logger.info(
            f"  {sym:12s} | {best['strategy']:15s} | "
            f"sharpe:{best['sharpe_ratio']:+.3f} | "
            f"ret:{best['total_return_pct']:+7.2f}% | "
            f"dd:{best['max_drawdown_pct']:5.1f}%"
        )

    # 保存
    output = Path("reports") / "out_of_sample_test.csv"
    df.to_csv(output, index=False)
    logger.info(f"\n保存到: {output}")
    logger.success("样本外验证完成！")


if __name__ == "__main__":
    main()
