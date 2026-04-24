"""Z-Score 均值回归：用统计方法精确衡量偏离程度。

当价格偏离均值超过 N 个标准差时入场。
比简单的 BB 更精确，因为 Z-Score 标准化了偏离幅度。
结合「本地极值倾向回归」的 QuantPedia 研究发现。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ZScoreReversionStrategy(StrategyBase):
    """Z-Score 均值回归。

    入场：Z-Score < -threshold（价格低于均值 N 个标准差）
          + 企稳确认（Z-Score 回升）
    出场：Z-Score 回到 0 附近（回到均值）
    """

    @property
    def name(self) -> str:
        return "zscore_reversion"

    def generate_signals(
        self,
        price: pd.Series,
        lookback: int = 100,
        entry_z: float = -2.0,
        exit_z: float = 0.0,
        confirm_bars: int = 2,
        trend_filter: bool = True,
        trend_ma: int = 200,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 Z-Score 信号。"""
        ma = price.rolling(window=lookback).mean()
        std = price.rolling(window=lookback).std()
        zscore = (price - ma) / std

        # 入场：Z-Score 从极低处回升（企稳确认）
        extreme_low = zscore < entry_z
        recovering = zscore > zscore.shift(1)
        # 连续回升确认
        recover_count = recovering.astype(int).rolling(window=confirm_bars).sum()
        stabilized = recover_count >= confirm_bars

        entries = extreme_low.shift(1).fillna(False) & stabilized

        # 趋势过滤：长期趋势不能是强下跌
        if trend_filter:
            long_ma = price.rolling(window=trend_ma).mean()
            # 允许在均线附近或上方（不要在暴跌趋势中抄底）
            ma_slope = (long_ma - long_ma.shift(20)) / long_ma.shift(20)
            not_strong_downtrend = ma_slope > -0.05  # 斜率不超过 -5%
            entries = entries & not_strong_downtrend

        # 出场：Z-Score 回到均值附近
        exits = (zscore > exit_z) & (zscore.shift(1) <= exit_z)

        entries = entries & (~entries.shift(1).fillna(False))
        exits = exits.fillna(False)
        entries = entries.fillna(False)

        logger.debug(
            f"ZScore | lookback={lookback} entry_z={entry_z} exit_z={exit_z} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def zscore_reversion_signal(
    price: pd.Series,
    lookback: int = 100,
    entry_z: float = -2.0,
    exit_z: float = 0.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return ZScoreReversionStrategy().generate_signals(price, lookback=lookback, entry_z=entry_z, exit_z=exit_z)
