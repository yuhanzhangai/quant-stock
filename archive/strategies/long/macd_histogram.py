"""MACD Histogram 动量策略：柱状图方向 + 零轴交叉。

MACD histogram 正且递增 = 强多头动量。
histogram 由正转负 = 动量衰竭，出场。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MACDHistogramStrategy(StrategyBase):
    """MACD Histogram 策略。

    入场：histogram 从负转正（零轴上穿）+ 趋势确认
    出场：histogram 从正转负（零轴下穿）
    可选：histogram 加速度确认（连续 N 根递增）
    """

    @property
    def name(self) -> str:
        return "macd_histogram"

    def generate_signals(
        self,
        price: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        trend_ma: int = 200,
        use_trend: bool = True,
        accel_bars: int = 2,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 MACD histogram 信号。"""
        ema_fast = price.ewm(span=fast, adjust=False).mean()
        ema_slow = price.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        # 零轴上穿：histogram 从负转正
        cross_up = (histogram > 0) & (histogram.shift(1) <= 0)

        # histogram 加速度：连续递增
        hist_increasing = histogram > histogram.shift(1)
        if accel_bars > 1:
            accel = hist_increasing.rolling(window=accel_bars).sum() >= accel_bars
        else:
            accel = hist_increasing

        # 趋势过滤
        if use_trend and trend_ma > 0:
            ma = price.rolling(window=trend_ma).mean()
            trend_ok = price > ma
        else:
            trend_ok = pd.Series(True, index=price.index)

        entries = cross_up & trend_ok
        # 也可以在 histogram 加速时追加入场
        accel_entry = accel & (histogram > 0) & trend_ok
        accel_entry = accel_entry & (~accel_entry.shift(1).fillna(False))
        entries = entries | accel_entry

        # 出场：histogram 零轴下穿
        exits = (histogram < 0) & (histogram.shift(1) >= 0)

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MACD_Hist | fast={fast} slow={slow} sig={signal} "
            f"trend={use_trend} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def macd_histogram_signal(
    price: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    trend_ma: int = 200,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MACDHistogramStrategy().generate_signals(price, fast=fast, slow=slow, signal=signal, trend_ma=trend_ma)
