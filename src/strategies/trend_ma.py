"""双均线趋势策略。"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class TrendMAStrategy(StrategyBase):
    """双均线策略。

    短均线上穿长均线开多，死叉平仓。
    """

    @property
    def name(self) -> str:
        return "trend_ma"

    def generate_signals(
        self,
        price: pd.Series,
        short_window: int = 10,
        long_window: int = 50,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成双均线交叉信号。

        Args:
            price: 收盘价序列
            short_window: 短均线周期
            long_window: 长均线周期

        Returns:
            (entries, exits) 布尔信号
        """
        short_ma = price.rolling(window=short_window).mean()
        long_ma = price.rolling(window=long_window).mean()

        # 金叉：短均线从下方穿越长均线
        entries = (short_ma > long_ma) & (short_ma.shift(1) <= long_ma.shift(1))

        # 死叉：短均线从上方穿越长均线
        exits = (short_ma < long_ma) & (short_ma.shift(1) >= long_ma.shift(1))

        # 填充 NaN
        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"TrendMA 信号 | short={short_window} long={long_window} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )

        return entries, exits


def trend_ma_signal_func(
    price: pd.Series, short_window: int = 10, long_window: int = 50
) -> tuple[pd.Series, pd.Series]:
    """独立函数版本，用于网格搜索。"""
    strategy = TrendMAStrategy()
    return strategy.generate_signals(price, short_window=short_window, long_window=long_window)
