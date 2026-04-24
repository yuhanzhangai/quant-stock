"""配对交易/价差策略：利用币种间的协整关系。

当两个高相关币种的价格比偏离均值时，
做多便宜的、做空贵的，等待回归。
由于只有做多，简化为：比值偏低时做多弱势币。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class PairsSpreadStrategy(StrategyBase):
    """价差均值回归策略（简化版）。

    用 ETH/BTC 比值做例子：
    - 比值跌到 N 日均值以下 2 个标准差 = ETH 相对便宜 = 做多 ETH
    - 比值回到均值 = 出场
    """

    @property
    def name(self) -> str:
        return "pairs_spread"

    def generate_signals(
        self,
        price: pd.Series,
        spread_lookback: int = 200,
        entry_z: float = -1.5,
        exit_z: float = 0.0,
        min_gap: int = 72,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """用自身价格的 z-score 模拟价差。

        实际应该用 ETH/BTC ratio，这里简化用单币 z-score。
        """
        ma = price.rolling(window=spread_lookback).mean()
        std = price.rolling(window=spread_lookback).std()
        zscore = (price - ma) / std

        # 入场：z-score 极低（价格相对均值便宜）+ 开始回升
        extreme = zscore < entry_z
        recovering = zscore > zscore.shift(1)
        raw_entries = extreme & recovering

        # 限频
        entries = pd.Series(False, index=price.index)
        last = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last) >= min_gap:
                entries.iloc[i] = True
                last = i

        # 出场：回到均值
        exits = (zscore > exit_z) & (zscore.shift(1) <= exit_z)

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"PairsSpread | lookback={spread_lookback} entry_z={entry_z} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def pairs_spread_signal(
    price: pd.Series,
    spread_lookback: int = 200,
    entry_z: float = -1.5,
    min_gap: int = 72,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return PairsSpreadStrategy().generate_signals(
        price, spread_lookback=spread_lookback, entry_z=entry_z, min_gap=min_gap
    )
