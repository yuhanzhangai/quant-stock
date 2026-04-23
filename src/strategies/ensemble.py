"""策略 Ensemble：组合多策略信号投票。

组合 Top 3 策略（TrendMA_Filtered + AggressiveMom + RSIExtreme）
多数投票决定入场/出场，减少单策略过拟合风险。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class EnsembleStrategy(StrategyBase):
    """策略集成。

    汇集多个子策略的信号，用投票机制决定最终信号。
    - 至少 min_agree 个策略同意才入场
    - 至少 1 个策略发出出场信号即出场（保守出场）
    """

    @property
    def name(self) -> str:
        return "ensemble"

    def generate_signals(
        self,
        price: pd.Series,
        min_agree: int = 2,
        # TrendMA_Filtered 参数
        tf_short: int = 25,
        tf_long: int = 200,
        tf_atr_mult: float = 0.5,
        # AggressiveMom 参数
        am_lookback: int = 50,
        am_consec: int = 4,
        am_trail: float = 1.5,
        # RSI 参数
        rsi_period: int = 14,
        rsi_oversold: int = 25,
        rsi_overbought: int = 75,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成集成信号。"""
        from src.strategies.trend_ma_filtered import TrendMAFilteredStrategy
        from src.strategies.aggressive_momentum import AggressiveMomentumStrategy
        from src.strategies.rsi_extreme import RSIExtremeStrategy

        # 子策略信号
        e1, x1 = TrendMAFilteredStrategy().generate_signals(
            price, short_window=tf_short, long_window=tf_long, atr_mult=tf_atr_mult
        )
        e2, x2 = AggressiveMomentumStrategy().generate_signals(
            price, lookback=am_lookback, consec_bars=am_consec, trail_atr_mult=am_trail
        )
        e3, x3 = RSIExtremeStrategy().generate_signals(
            price, rsi_period=rsi_period, oversold=rsi_oversold, overbought=rsi_overbought
        )

        # 投票
        entry_votes = e1.astype(int) + e2.astype(int) + e3.astype(int)
        exit_votes = x1.astype(int) + x2.astype(int) + x3.astype(int)

        # 入场：至少 min_agree 个策略同意
        entries = entry_votes >= min_agree

        # 出场：任一策略出场（保守）
        exits = exit_votes >= 1

        # 边沿触发
        entries = entries & (~entries.shift(1).fillna(False))
        exits = exits & (~exits.shift(1).fillna(False))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"Ensemble | min_agree={min_agree} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def ensemble_signal(
    price: pd.Series, min_agree: int = 2,
    tf_short: int = 25, tf_long: int = 200,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return EnsembleStrategy().generate_signals(
        price, min_agree=min_agree, tf_short=tf_short, tf_long=tf_long
    )
