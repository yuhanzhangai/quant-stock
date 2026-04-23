"""分钟线专用策略：大级别趋势过滤 + 小级别精确入场。

核心思路：
- 用长周期 MA（如 200 根 15m = 50 小时）判断大趋势方向
- 只在大趋势方向上做单（不逆势）
- 用 RSI 超卖 + 价格回踩支撑位精确入场
- 严格限制交易次数（<15 笔/月）减少手续费损耗
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ScalpTrendStrategy(StrategyBase):
    """分钟线趋势择时策略。

    只在大级别多头趋势中，等待回调到支撑位后入场。
    """

    @property
    def name(self) -> str:
        return "scalp_trend"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 200,       # 大趋势判断（200*15m≈50h）
        support_ma: int = 20,      # 短期支撑（20*15m≈5h）
        rsi_period: int = 14,
        rsi_entry: int = 35,       # RSI 超卖入场
        rsi_exit: int = 70,        # RSI 超买出场
        min_gap_bars: int = 96,    # 两笔交易最少间隔（96*15m=24h）
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成分钟线信号。"""
        # 大趋势
        ma_long = price.rolling(window=trend_ma).mean()
        ma_short = price.rolling(window=support_ma).mean()
        uptrend = (price > ma_long) & (ma_long > ma_long.shift(20))

        # RSI
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # 入场：大趋势向上 + 回调到短均线附近 + RSI 超卖回升
        pullback = price < ma_short * 1.005  # 价格在短均线附近或下方
        rsi_bounce = (rsi > rsi_entry) & (rsi.shift(1) <= rsi_entry)
        raw_entries = uptrend & pullback & rsi_bounce

        # 限制交易频率：两笔间至少间隔 min_gap_bars
        entries = pd.Series(False, index=price.index)
        last_entry = -min_gap_bars * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry) >= min_gap_bars:
                entries.iloc[i] = True
                last_entry = i

        # 出场：RSI 超买 或 跌破趋势线
        rsi_overbought = (rsi > rsi_exit) & (rsi.shift(1) <= rsi_exit)
        trend_break = (price < ma_long) & (price.shift(1) >= ma_long.shift(1))
        exits = rsi_overbought | trend_break

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ScalpTrend | trend_ma={trend_ma} rsi_entry={rsi_entry} "
            f"gap={min_gap_bars} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def scalp_trend_signal(
    price: pd.Series, trend_ma: int = 200, rsi_entry: int = 35,
    min_gap_bars: int = 96, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return ScalpTrendStrategy().generate_signals(
        price, trend_ma=trend_ma, rsi_entry=rsi_entry, min_gap_bars=min_gap_bars
    )
