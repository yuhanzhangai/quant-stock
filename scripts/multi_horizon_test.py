"""多时间跨度 + 市场事件期间验证。

测试不同持有期（1周/1月/3月/6月）+ 高波动事件期的策略表现。
验证策略在各种市场环境下的鲁棒性。
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategies.aggressive_momentum import aggressive_momentum_signal
from src.strategies.ichimoku_momentum import ichimoku_momentum_signal
from src.strategies.trend_ma_filtered import trend_ma_filtered_signal

from config.settings import get_settings
from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter

# 只测通过 OOS 验证的 3 个 ROBUST 策略
ROBUST_STRATEGIES = {
    "IchiMom_v2": (ichimoku_momentum_signal, {"tenkan": 9, "kijun": 26, "lookback": 50, "consec_bars": 4}),
    "AggrMom": (aggressive_momentum_signal, {"lookback": 50, "consec_bars": 4, "trail_atr_mult": 1.5}),
    "TrendMA_Filt": (trend_ma_filtered_signal, {"short_window": 25, "long_window": 200, "atr_mult": 0.5}),
}

COINS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT"]

# 不同时间跨度（4h K线数）
HORIZONS = {
    "1周": 42,  # 7 * 6
    "1月": 180,  # 30 * 6
    "3月": 540,  # 90 * 6
    "6月": 1095,  # 182 * 6
    "1年": 2190,  # 365 * 6
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


def detect_high_volatility_periods(price: pd.Series, threshold: float = 2.0) -> list[tuple[int, int]]:
    """检测高波动事件期间（日收益率 > 2 倍标准差的时段）。"""
    returns = price.pct_change()
    vol = returns.rolling(window=100).std()
    extreme = returns.abs() > vol * threshold

    # 找连续高波动区间
    periods = []
    in_event = False
    start = 0
    for i in range(len(extreme)):
        if extreme.iloc[i] and not in_event:
            start = max(0, i - 50)  # 事件前 50 根
            in_event = True
        elif not extreme.iloc[i] and in_event:
            end = min(len(price), i + 50)  # 事件后 50 根
            if end - start >= 100:
                periods.append((start, end))
            in_event = False
    return periods


def main() -> None:
    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="4h")
    all_rows = []

    logger.info(f"\n{'=' * 80}")
    logger.info("多时间跨度验证 | 3 ROBUST 策略 x 4 主流币")
    logger.info(f"{'=' * 80}")

    for sym in COINS:
        price = load_price(sym, "4h")
        if price is None:
            continue

        # 1. 滑动窗口多跨度验证
        for horizon_name, horizon_bars in HORIZONS.items():
            if len(price) < horizon_bars + 100:
                continue

            sharpes = []
            # 在数据上滑动多个不重叠窗口
            n_windows = max(1, (len(price) - 100) // horizon_bars)
            for w in range(min(n_windows, 8)):  # 最多 8 个窗口
                start = w * horizon_bars
                end = start + horizon_bars
                if end > len(price):
                    break
                chunk = price.iloc[start:end]

                for sname, (func, params) in ROBUST_STRATEGIES.items():
                    try:
                        e, x = func(chunk, **params)
                        pf = engine.run(chunk, e, x)
                        m = compute_metrics(pf)
                        sharpes.append(m["sharpe_ratio"])
                        all_rows.append(
                            {
                                "symbol": sym,
                                "strategy": sname,
                                "horizon": horizon_name,
                                "window": w,
                                **m,
                            }
                        )
                    except Exception:
                        pass

        # 2. 高波动事件期间验证
        events = detect_high_volatility_periods(price)
        for ei, (start, end) in enumerate(events[:5]):  # 最多 5 个事件
            chunk = price.iloc[start:end]
            if len(chunk) < 50:
                continue
            f"{chunk.index[0].strftime('%Y-%m-%d')}~{chunk.index[-1].strftime('%Y-%m-%d')}"

            for sname, (func, params) in ROBUST_STRATEGIES.items():
                try:
                    e, x = func(chunk, **params)
                    pf = engine.run(chunk, e, x)
                    m = compute_metrics(pf)
                    all_rows.append(
                        {
                            "symbol": sym,
                            "strategy": sname,
                            "horizon": f"EVENT_{ei}",
                            "window": -1,
                            **m,
                        }
                    )
                except Exception:
                    pass

    df = pd.DataFrame(all_rows)

    # 分析：按跨度汇总
    logger.info(f"\n{'=' * 80}")
    logger.info("按时间跨度平均表现")
    logger.info(f"{'=' * 80}")

    normal = df[~df["horizon"].str.startswith("EVENT")]
    for sname in ROBUST_STRATEGIES:
        logger.info(f"\n  --- {sname} ---")
        for horizon in HORIZONS:
            subset = normal[(normal["strategy"] == sname) & (normal["horizon"] == horizon)]
            if subset.empty:
                continue
            avg_s = subset["sharpe_ratio"].mean()
            pos = (subset["sharpe_ratio"] > 0).mean() * 100
            logger.info(f"    {horizon:4s} | avg_sharpe:{avg_s:+.3f} | positive:{pos:4.0f}% | n={len(subset)}")

    # 事件期间
    events_df = df[df["horizon"].str.startswith("EVENT")]
    if not events_df.empty:
        logger.info(f"\n{'=' * 80}")
        logger.info("高波动事件期间表现")
        logger.info(f"{'=' * 80}")
        for sname in ROBUST_STRATEGIES:
            subset = events_df[events_df["strategy"] == sname]
            if subset.empty:
                continue
            avg_s = subset["sharpe_ratio"].mean()
            pos = (subset["sharpe_ratio"] > 0).mean() * 100
            logger.info(f"  {sname:15s} | avg_sharpe:{avg_s:+.3f} | positive:{pos:4.0f}% | n={len(subset)}")

    # 保存
    output = Path("reports") / "multi_horizon_test.csv"
    df.to_csv(output, index=False)
    logger.info(f"\n保存到: {output}")
    logger.success("多跨度验证完成！")


if __name__ == "__main__":
    main()
