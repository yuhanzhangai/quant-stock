"""动量突破策略：价格突破 N 日高点开仓，跌破 N 日低点平仓。"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MomentumBreakoutStrategy(StrategyBase):
    """Donchian 通道突破策略。

    - 价格突破最近 N 根 K 线的最高价 -> 开多
    - 价格跌破最近 M 根 K 线的最低价 -> 平仓
    - N > M，让入场更严格、出场更快
    """

    @property
    def name(self) -> str:
        return "momentum_breakout"

    def generate_signals(
        self,
        price: pd.Series,
        entry_window: int = 50,
        exit_window: int = 20,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成突破信号。"""
        upper = price.rolling(window=entry_window).max()
        lower = price.rolling(window=exit_window).min()

        # 突破上轨入场
        entries = (price > upper.shift(1)) & (price.shift(1) <= upper.shift(2))
        # 跌破下轨出场
        exits = (price < lower.shift(1)) & (price.shift(1) >= lower.shift(2))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MomentumBreakout | entry={entry_window} exit={exit_window} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def momentum_breakout_signal(
    price: pd.Series,
    entry_window: int = 50,
    exit_window: int = 20,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """独立函数版本。"""
    strategy = MomentumBreakoutStrategy()
    return strategy.generate_signals(price, entry_window=entry_window, exit_window=exit_window)
