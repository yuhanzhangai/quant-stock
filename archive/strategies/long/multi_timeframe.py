"""多周期确认策略：4h 信号 + 模拟 1d 趋势过滤。

核心思路：用更长的均线模拟日线趋势方向，
只在日线趋势向上时接受 4h 的做多信号。
减少逆势交易。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MultiTimeframeStrategy(StrategyBase):
    """多周期确认。

    高周期过滤（模拟日线）：
    - price > MA(150) 且 MA(150) 上升 -> 日线多头

    低周期入场（4h）：
    - RSI 从超卖回升 或 短均线金叉
    - 只在日线多头时才做多
    """

    @property
    def name(self) -> str:
        return "multi_timeframe"

    def generate_signals(
        self,
        price: pd.Series,
        daily_ma: int = 150,  # 150 * 4h = 600h ≈ 25d
        short_ma: int = 10,
        long_ma: int = 40,
        rsi_period: int = 14,
        rsi_oversold: int = 30,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成多周期信号。"""
        # 模拟日线趋势
        ma_daily = price.rolling(window=daily_ma).mean()
        daily_uptrend = (price > ma_daily) & (ma_daily > ma_daily.shift(10))

        # 4h 入场信号 1: 短均线金叉
        sma_short = price.rolling(window=short_ma).mean()
        sma_long = price.rolling(window=long_ma).mean()
        ma_cross = (sma_short > sma_long) & (sma_short.shift(1) <= sma_long.shift(1))

        # 4h 入场信号 2: RSI 超卖回升
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        rsi_bounce = (rsi > rsi_oversold) & (rsi.shift(1) <= rsi_oversold)

        # 组合：日线多头 + (金叉 或 RSI回升)
        entries = daily_uptrend & (ma_cross | rsi_bounce)

        # 出场：跌破日线均线 或 RSI 超买
        exits_trend = (price < ma_daily) & (price.shift(1) >= ma_daily.shift(1))
        exits_rsi = (rsi > 75) & (rsi.shift(1) <= 75)
        exits = exits_trend | exits_rsi

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MultiTF | daily_ma={daily_ma} short={short_ma} long={long_ma} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def multi_timeframe_signal(
    price: pd.Series,
    daily_ma: int = 150,
    short_ma: int = 10,
    long_ma: int = 40,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MultiTimeframeStrategy().generate_signals(price, daily_ma=daily_ma, short_ma=short_ma, long_ma=long_ma)
