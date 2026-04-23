"""SuperTrend 策略：ATR 自适应趋势跟踪 + 内置移动止损。

SuperTrend = ATR 通道 + 趋势翻转检测。
价格在 SuperTrend 上方 = 多头，下方 = 空头。
翻转时入场/出场，天然带移动止损。
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase


def calc_supertrend(
    price: pd.Series, atr_period: int = 14, multiplier: float = 3.0
) -> tuple[pd.Series, pd.Series]:
    """计算 SuperTrend 指标。

    Returns:
        (supertrend_line, direction): direction=1 多头, direction=-1 空头
    """
    high = price.rolling(2).max()
    low = price.rolling(2).min()
    prev_close = price.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=atr_period).mean()

    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    supertrend = pd.Series(np.nan, index=price.index)
    direction = pd.Series(1, index=price.index)

    for i in range(1, len(price)):
        if pd.isna(upper_band.iloc[i]):
            continue

        # 调整 bands（只能朝有利方向移动）
        if lower_band.iloc[i] < lower_band.iloc[i - 1] and price.iloc[i - 1] > lower_band.iloc[i - 1]:
            lower_band.iloc[i] = lower_band.iloc[i - 1]
        if upper_band.iloc[i] > upper_band.iloc[i - 1] and price.iloc[i - 1] < upper_band.iloc[i - 1]:
            upper_band.iloc[i] = upper_band.iloc[i - 1]

        if direction.iloc[i - 1] == 1:
            if price.iloc[i] < lower_band.iloc[i]:
                direction.iloc[i] = -1
                supertrend.iloc[i] = upper_band.iloc[i]
            else:
                direction.iloc[i] = 1
                supertrend.iloc[i] = lower_band.iloc[i]
        else:
            if price.iloc[i] > upper_band.iloc[i]:
                direction.iloc[i] = 1
                supertrend.iloc[i] = lower_band.iloc[i]
            else:
                direction.iloc[i] = -1
                supertrend.iloc[i] = upper_band.iloc[i]

    return supertrend, direction


class SuperTrendStrategy(StrategyBase):
    """SuperTrend 趋势跟踪策略。

    入场：方向从 -1 翻转到 1（空转多）
    出场：方向从 1 翻转到 -1（多转空）
    可选 ADX 过滤：只在趋势强度 > 阈值时交易
    """

    @property
    def name(self) -> str:
        return "supertrend"

    def generate_signals(
        self,
        price: pd.Series,
        atr_period: int = 14,
        multiplier: float = 3.0,
        adx_period: int = 14,
        adx_threshold: int = 20,
        use_adx: bool = True,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 SuperTrend 信号。"""
        _, direction = calc_supertrend(price, atr_period, multiplier)

        # 翻转信号
        entries = (direction == 1) & (direction.shift(1) == -1)
        exits = (direction == -1) & (direction.shift(1) == 1)

        # ADX 趋势强度过滤
        if use_adx:
            adx = self._calc_adx(price, adx_period)
            entries = entries & (adx > adx_threshold)

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"SuperTrend | atr={atr_period} mult={multiplier} "
            f"adx={use_adx}>{adx_threshold} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits

    @staticmethod
    def _calc_adx(price: pd.Series, period: int = 14) -> pd.Series:
        """简化 ADX 计算（用价格变化代替 DM）。"""
        change = price.diff().abs()
        avg_change = change.rolling(window=period).mean()
        avg_price = price.rolling(window=period).mean()
        # 伪 ADX：变化幅度 / 价格 * 100
        adx_proxy = (avg_change / avg_price) * 100 * 10
        return adx_proxy


def supertrend_signal(
    price: pd.Series, atr_period: int = 14, multiplier: float = 3.0,
    adx_threshold: int = 20, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return SuperTrendStrategy().generate_signals(
        price, atr_period=atr_period, multiplier=multiplier, adx_threshold=adx_threshold
    )
