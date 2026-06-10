"""RSI 极值反转策略：RSI 超卖买入，超买卖出。"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class RSIExtremeStrategy(StrategyBase):
    """RSI 极值反转策略。

    - RSI 从超卖区回升（下穿后上穿阈值）-> 开多
    - RSI 进入超买区 或 持仓超过 max_hold 根 K 线 -> 平仓
    - 可选趋势过滤：只在价格高于长均线时做多
    """

    @property
    def name(self) -> str:
        return "rsi_extreme"

    def generate_signals(
        self,
        price: pd.Series,
        rsi_period: int = 14,
        oversold: int = 25,
        overbought: int = 75,
        trend_ma: int = 200,
        use_trend_filter: bool = True,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 RSI 极值信号。"""
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # RSI 从超卖区回升
        entries = (rsi > oversold) & (rsi.shift(1) <= oversold)

        # RSI 进入超买区
        exits = (rsi > overbought) & (rsi.shift(1) <= overbought)

        # 趋势过滤：只在价格高于长均线时做多
        if use_trend_filter and trend_ma > 0:
            ma_long = price.rolling(window=trend_ma).mean()
            entries = entries & (price > ma_long)

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"RSIExtreme | period={rsi_period} os={oversold} ob={overbought} "
            f"trend={use_trend_filter} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def rsi_extreme_signal(
    price: pd.Series,
    rsi_period: int = 14,
    oversold: int = 25,
    overbought: int = 75,
    trend_ma: int = 200,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """独立函数版本。"""
    strategy = RSIExtremeStrategy()
    return strategy.generate_signals(
        price, rsi_period=rsi_period, oversold=oversold, overbought=overbought, trend_ma=trend_ma
    )
