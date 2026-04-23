"""经典海龟交易策略：Donchian 通道突破 + ATR 仓位管理。

Richard Dennis 的经典系统，适合趋势市场。
使用两个时间窗口：快系统（20日）入场，慢系统（55日）备用。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class TurtleTradingStrategy(StrategyBase):
    """经典海龟交易策略。

    System 1 (快):
    - 价格突破 20 日最高价 -> 入场
    - 价格跌破 10 日最低价 -> 出场

    System 2 (慢):
    - 价格突破 55 日最高价 -> 入场
    - 价格跌破 20 日最低价 -> 出场

    ATR 止损：入场价 - 2 * ATR
    """

    @property
    def name(self) -> str:
        return "turtle_trading"

    def generate_signals(
        self,
        price: pd.Series,
        entry_period: int = 20,
        exit_period: int = 10,
        atr_period: int = 20,
        atr_stop_mult: float = 2.0,
        use_atr_stop: bool = True,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成海龟信号。"""
        # Donchian 通道
        entry_high = price.rolling(window=entry_period).max()
        exit_low = price.rolling(window=exit_period).min()

        # ATR
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # 入场：突破 entry_period 日新高
        entries = (price > entry_high.shift(1)) & (price.shift(1) <= entry_high.shift(2))

        # 出场：跌破 exit_period 日新低 OR ATR 止损
        basic_exit = (price < exit_low.shift(1)) & (price.shift(1) >= exit_low.shift(2))

        if use_atr_stop:
            # 移动止损：最近高点 - ATR * mult
            recent_high = price.rolling(window=entry_period).max()
            atr_stop = recent_high - atr * atr_stop_mult
            exits = basic_exit | (price < atr_stop)
        else:
            exits = basic_exit

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"Turtle | entry={entry_period} exit={exit_period} "
            f"atr_stop={use_atr_stop}x{atr_stop_mult} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def turtle_signal(
    price: pd.Series, entry_period: int = 20, exit_period: int = 10,
    atr_stop_mult: float = 2.0, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return TurtleTradingStrategy().generate_signals(
        price, entry_period=entry_period, exit_period=exit_period, atr_stop_mult=atr_stop_mult
    )
