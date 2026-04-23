"""找出数据中的熊市区间，用于做空策略的定向测试。

分析方法：
1. 用 4h 数据（2024-2026 三年）找大趋势
2. 用 5m 数据找最近 3 个月的微观熊市段
3. 标记每个币种的熊市起止时间
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings


COINS = ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"]


def load_price(symbol: str, tf: str) -> pd.Series | None:
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, tf)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


def find_bear_periods(price: pd.Series, ma_period: int = 50, min_length: int = 20) -> list[dict]:
    """找出价格持续低于 MA 且 MA 下行的熊市区间。"""
    ma = price.rolling(window=ma_period).mean()
    ma_slope = ma - ma.shift(5)

    bear = (price < ma) & (ma_slope < 0)

    periods = []
    in_bear = False
    start_idx = 0

    for i in range(len(bear)):
        if bear.iloc[i] and not in_bear:
            start_idx = i
            in_bear = True
        elif not bear.iloc[i] and in_bear:
            length = i - start_idx
            if length >= min_length:
                seg = price.iloc[start_idx:i]
                ret = (seg.iloc[-1] - seg.iloc[0]) / seg.iloc[0] * 100
                periods.append({
                    "start": price.index[start_idx],
                    "end": price.index[i - 1],
                    "length": length,
                    "return_pct": ret,
                    "start_price": seg.iloc[0],
                    "end_price": seg.iloc[-1],
                    "min_price": seg.min(),
                    "max_drawdown": (seg.min() - seg.iloc[0]) / seg.iloc[0] * 100,
                })
            in_bear = False

    # 处理结尾还在熊市的情况
    if in_bear:
        length = len(bear) - start_idx
        if length >= min_length:
            seg = price.iloc[start_idx:]
            ret = (seg.iloc[-1] - seg.iloc[0]) / seg.iloc[0] * 100
            periods.append({
                "start": price.index[start_idx],
                "end": price.index[-1],
                "length": length,
                "return_pct": ret,
                "start_price": seg.iloc[0],
                "end_price": seg.iloc[-1],
                "min_price": seg.min(),
                "max_drawdown": (seg.min() - seg.iloc[0]) / seg.iloc[0] * 100,
            })

    return sorted(periods, key=lambda x: x["return_pct"])  # 跌幅最大的排前面


def main() -> None:
    print("=" * 100)
    print("  熊市区间扫描 — 找出做空策略的最佳测试时段")
    print("=" * 100)

    # === 4h 数据：长期熊市 ===
    print("\n\n[1] 4h 数据 — 长期熊市区间（MA50=200h≈8天）")
    print("─" * 100)

    for coin in COINS:
        price_4h = load_price(coin, "4h")
        if price_4h is None:
            print(f"\n  {coin}: 无 4h 数据")
            continue

        coin_short = coin.replace("-USDT", "")
        print(f"\n  {coin_short} | 4h 数据量: {len(price_4h)} | {price_4h.index[0].date()} ~ {price_4h.index[-1].date()}")

        periods = find_bear_periods(price_4h, ma_period=50, min_length=30)

        if not periods:
            print(f"    没有找到显著熊市区间")
            continue

        print(f"    {'起始':>12} | {'结束':>12} | {'长度':>6} | {'跌幅%':>8} | {'最大回撤%':>9} | {'起始价':>10} | {'结束价':>10}")
        for p in periods[:5]:  # 前 5 个最大跌幅
            print(
                f"    {str(p['start'].date()):>12} | {str(p['end'].date()):>12} | "
                f"{p['length']:>6} | {p['return_pct']:>+8.2f} | {p['max_drawdown']:>9.2f} | "
                f"${p['start_price']:>9.2f} | ${p['end_price']:>9.2f}"
            )

    # === 5m 数据：微观熊市 ===
    print(f"\n\n[2] 5m 数据 — 微观熊市区间（MA180=15h）")
    print("─" * 100)

    all_bear_segments = {}

    for coin in COINS:
        price_5m = load_price(coin, "5m")
        if price_5m is None:
            print(f"\n  {coin}: 无 5m 数据")
            continue

        coin_short = coin.replace("-USDT", "")
        print(f"\n  {coin_short} | 5m 数据量: {len(price_5m)} | {price_5m.index[0]} ~ {price_5m.index[-1]}")

        periods = find_bear_periods(price_5m, ma_period=180, min_length=200)
        all_bear_segments[coin_short] = periods

        if not periods:
            print(f"    没有找到显著熊市区间（MA180）")
            continue

        total_bear_candles = sum(p["length"] for p in periods)
        bear_pct = total_bear_candles / len(price_5m) * 100

        print(f"    总熊市占比: {bear_pct:.1f}% ({total_bear_candles}/{len(price_5m)} 根K线)")
        print(f"    {'#':>4} | {'起始':>20} | {'结束':>20} | {'时长(h)':>8} | {'跌幅%':>8} | {'最大回撤%':>9}")

        for idx, p in enumerate(periods[:8], 1):
            hours = p["length"] * 5 / 60
            print(
                f"    {idx:>4} | {str(p['start']):>20} | {str(p['end']):>20} | "
                f"{hours:>8.1f} | {p['return_pct']:>+8.2f} | {p['max_drawdown']:>9.2f}"
            )

    # === 找共同熊市时段（所有币种同时下跌）===
    print(f"\n\n[3] 市场整体熊市程度分析（5m 数据）")
    print("─" * 100)

    for coin in COINS:
        price = load_price(coin, "5m")
        if price is None:
            continue

        coin_short = coin.replace("-USDT", "")

        # 按周统计涨跌
        weekly_returns = price.resample("W").last().pct_change() * 100
        bear_weeks = (weekly_returns < -3).sum()
        total_weeks = len(weekly_returns.dropna())

        print(f"\n  {coin_short}: {bear_weeks}/{total_weeks} 周跌幅>3%")

        # 最近走势
        if len(price) >= 288:  # 至少 1 天
            last_24h = (price.iloc[-1] - price.iloc[-288]) / price.iloc[-288] * 100
            last_week = (price.iloc[-1] - price.iloc[-min(2016, len(price))]) / price.iloc[-min(2016, len(price))] * 100
            print(f"    最近24h: {last_24h:+.2f}% | 最近1周: {last_week:+.2f}%")

    print(f"\n{'=' * 100}")
    print("  扫描完成！用这些熊市区间来验证做空策略")
    print(f"{'=' * 100}")


if __name__ == "__main__":
    main()
