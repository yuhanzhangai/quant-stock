"""网格交易策略：适合横盘震荡的分钟线策略。

在价格区间内设置等距网格线，价格触及下方网格买入，触及上方网格卖出。
核心优势：不需要预测方向，只需要波动即可盈利。
适合：低波动横盘期、分钟线。
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase


class GridTradingStrategy(StrategyBase):
    """网格交易策略。

    动态网格：以移动平均为中心，ATR 为网格间距。
    价格跌到中心以下 N 个网格 -> 买入
    价格涨到中心以上 N 个网格 -> 卖出
    """

    @property
    def name(self) -> str:
        return "grid_trading"

    def generate_signals(
        self,
        price: pd.Series,
        ma_period: int = 100,
        atr_period: int = 14,
        grid_mult: float = 1.0,
        entry_grids: int = 2,
        exit_grids: int = 1,
        cooldown: int = 24,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成网格信号。

        Args:
            ma_period: 中心线周期
            atr_period: ATR 周期（决定网格间距）
            grid_mult: 网格间距 = ATR * mult
            entry_grids: 跌到中心下方 N 个网格买入
            exit_grids: 涨到中心上方 N 个网格卖出
            cooldown: 两笔间最小间隔
        """
        center = price.rolling(window=ma_period).mean()

        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat([
            high - low, (high - prev_close).abs(), (low - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        grid_size = atr * grid_mult

        # 价格偏离中心的网格数
        deviation = (price - center) / grid_size

        # 入场：偏离 < -entry_grids（价格低于中心 N 个网格）
        raw_entries = deviation < -entry_grids
        # 出场：偏离 > exit_grids（价格高于中心 N 个网格）
        raw_exits = deviation > exit_grids

        # 限制频率
        entries = pd.Series(False, index=price.index)
        last_trade = -cooldown * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_trade) >= cooldown:
                entries.iloc[i] = True
                last_trade = i

        exits = raw_exits & (~raw_exits.shift(1).fillna(False))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"Grid | ma={ma_period} grid={grid_mult}xATR "
            f"entry={entry_grids} exit={exit_grids} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def grid_trading_signal(
    price: pd.Series, ma_period: int = 100, grid_mult: float = 1.0,
    entry_grids: int = 2, cooldown: int = 24,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return GridTradingStrategy().generate_signals(
        price, ma_period=ma_period, grid_mult=grid_mult,
        entry_grids=entry_grids, cooldown=cooldown
    )
