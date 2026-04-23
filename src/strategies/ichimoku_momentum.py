"""Ichimoku + AggressiveMom 组合：两大稳定策略的融合。

Ichimoku 提供趋势方向确认（100% 正率），
AggressiveMom 提供精确入场时机（最高夏普）。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase
from src.strategies.ichimoku import IchimokuStrategy
from src.strategies.aggressive_momentum import AggressiveMomentumStrategy


class IchimokuMomentumStrategy(StrategyBase):
    """Ichimoku 确认 + 动量入场。

    入场：Ichimoku 多头区域 + AggressiveMom 信号触发
    出场：任一出场信号
    """

    @property
    def name(self) -> str:
        return "ichimoku_momentum"

    def generate_signals(
        self,
        price: pd.Series,
        tenkan: int = 9,
        kijun: int = 26,
        senkou_b: int = 52,
        lookback: int = 30,
        consec_bars: int = 3,
        trail_atr_mult: float = 2.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成组合信号。"""
        # Ichimoku 信号
        ichi = IchimokuStrategy()
        e_ichi, x_ichi = ichi.generate_signals(
            price, tenkan=tenkan, kijun=kijun, senkou_b=senkou_b
        )

        # Ichimoku 多头状态（不只是入场信号，而是持续状态）
        tenkan_line = (price.rolling(window=tenkan).max() + price.rolling(window=tenkan).min()) / 2
        kijun_line = (price.rolling(window=kijun).max() + price.rolling(window=kijun).min()) / 2
        senkou_a = ((tenkan_line + kijun_line) / 2).shift(kijun)
        senkou_b_line = ((price.rolling(window=senkou_b).max() + price.rolling(window=senkou_b).min()) / 2).shift(kijun)
        cloud_top = pd.concat([senkou_a, senkou_b_line], axis=1).max(axis=1)
        ichi_bullish = (price > cloud_top) & (tenkan_line > kijun_line)

        # AggressiveMom 信号
        aggr = AggressiveMomentumStrategy()
        e_aggr, x_aggr = aggr.generate_signals(
            price, lookback=lookback, consec_bars=consec_bars, trail_atr_mult=trail_atr_mult
        )

        # 组合：Ichimoku 多头 + AggressiveMom 入场
        entries = e_aggr & ichi_bullish

        # 出场：任一出场
        exits = x_ichi | x_aggr

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"IchiMom | ichi_bull:{ichi_bullish.sum()} aggr_entry:{e_aggr.sum()} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def ichimoku_momentum_signal(
    price: pd.Series, tenkan: int = 9, kijun: int = 26,
    lookback: int = 30, consec_bars: int = 3,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return IchimokuMomentumStrategy().generate_signals(
        price, tenkan=tenkan, kijun=kijun, lookback=lookback, consec_bars=consec_bars
    )
