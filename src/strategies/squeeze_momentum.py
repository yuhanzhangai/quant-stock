"""Squeeze Momentum 策略 (TTM Squeeze / LazyBear)。

核心：BB 在 Keltner Channel 内部 = 波动率压缩（squeeze）。
Squeeze 释放时，顺着动量方向入场。
TradingView 上 76000 赞的超人气指标。
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase


class SqueezeMomentumStrategy(StrategyBase):
    """Squeeze Momentum 策略。

    Squeeze 检测：BB 上轨 < KC 上轨 = squeeze on
    入场：squeeze 释放（on -> off）+ 动量方向确认
    动量：线性回归偏差值（简化用 price - MA 的归一化）
    """

    @property
    def name(self) -> str:
        return "squeeze_momentum"

    def generate_signals(
        self,
        price: pd.Series,
        bb_period: int = 20,
        bb_mult: float = 2.0,
        kc_period: int = 20,
        kc_mult: float = 1.5,
        mom_period: int = 12,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 Squeeze Momentum 信号。"""
        # Bollinger Bands
        bb_mid = price.rolling(window=bb_period).mean()
        bb_std = price.rolling(window=bb_period).std()
        bb_upper = bb_mid + bb_mult * bb_std
        bb_lower = bb_mid - bb_mult * bb_std

        # Keltner Channel (EMA + ATR)
        kc_mid = price.ewm(span=kc_period, adjust=False).mean()
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat([
            high - low, (high - prev_close).abs(), (low - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=kc_period).mean()
        kc_upper = kc_mid + kc_mult * atr
        kc_lower = kc_mid - kc_mult * atr

        # Squeeze 检测：BB 在 KC 内部 = squeeze on
        squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
        squeeze_off = ~squeeze_on

        # Squeeze 释放边沿：从 on 变成 off
        squeeze_release = squeeze_off & squeeze_on.shift(1).fillna(False)

        # 动量（简化线性回归：用 price 相对 MA 的偏差）
        momentum = price - price.rolling(window=mom_period).mean()
        mom_positive = momentum > 0
        mom_negative = momentum < 0
        mom_increasing = momentum > momentum.shift(1)

        # 入场：squeeze 释放 或 动量从负转正（不要求同时发生）
        mom_cross_up = mom_positive & (~mom_positive.shift(1).fillna(True))
        entries = squeeze_release & mom_positive  # 释放时动量为正
        entries = entries | (mom_cross_up & squeeze_off)  # 或非squeeze期间动量翻正

        # 出场：动量变负
        exits = mom_negative & mom_positive.shift(1).fillna(False)  # 动量由正转负

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        n_squeeze = squeeze_on.sum()
        n_release = squeeze_release.sum()
        logger.debug(
            f"Squeeze | bb={bb_period}/{bb_mult} kc={kc_period}/{kc_mult} | "
            f"squeeze_bars:{n_squeeze} releases:{n_release} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def squeeze_momentum_signal(
    price: pd.Series, bb_period: int = 20, bb_mult: float = 2.0,
    kc_period: int = 20, kc_mult: float = 1.5,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return SqueezeMomentumStrategy().generate_signals(
        price, bb_period=bb_period, bb_mult=bb_mult, kc_period=kc_period, kc_mult=kc_mult
    )
