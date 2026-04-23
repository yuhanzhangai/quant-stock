"""Keltner 通道突破策略：基于 ATR 的自适应通道。

来源: quantifiedstrategies.com 报告 77% 胜率。
比布林带更平滑，减少假突破。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class KeltnerBreakoutStrategy(StrategyBase):
    """Keltner 通道突破。

    - 价格突破上轨 -> 开多（趋势确认）
    - 价格跌破中轨 -> 平仓
    - ATR 做通道宽度，比布林带标准差更稳定
    """

    @property
    def name(self) -> str:
        return "keltner_breakout"

    def generate_signals(
        self,
        price: pd.Series,
        ema_period: int = 20,
        atr_period: int = 14,
        atr_mult: float = 2.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 Keltner 通道突破信号。"""
        # EMA 中轨
        mid = price.ewm(span=ema_period, adjust=False).mean()

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

        upper = mid + atr_mult * atr
        lower = mid - atr_mult * atr

        # 突破上轨入场
        entries = (price > upper) & (price.shift(1) <= upper.shift(1))
        # 跌破中轨出场
        exits = (price < mid) & (price.shift(1) >= mid.shift(1))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"Keltner | ema={ema_period} atr={atr_period} mult={atr_mult} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def keltner_signal(
    price: pd.Series, ema_period: int = 20, atr_period: int = 14,
    atr_mult: float = 2.0, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return KeltnerBreakoutStrategy().generate_signals(
        price, ema_period=ema_period, atr_period=atr_period, atr_mult=atr_mult
    )
