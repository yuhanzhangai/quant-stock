"""Williams %R + Stochastic 组合策略。

Williams %R 和 Stochastic 都是超买超卖震荡指标，
组合使用可以互相确认，减少假信号。
加趋势过滤只做多头。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class WilliamsStochStrategy(StrategyBase):
    """Williams %R + Stochastic 双确认策略。

    入场：Williams %R < -80（超卖）+ Stoch K 从下向上穿 D + 价格 > MA
    出场：Williams %R > -20（超买）或 Stoch K 下穿 D
    """

    @property
    def name(self) -> str:
        return "williams_stoch"

    def generate_signals(
        self,
        price: pd.Series,
        wr_period: int = 14,
        stoch_k: int = 14,
        stoch_d: int = 3,
        ma_period: int = 100,
        wr_oversold: float = -80.0,
        wr_overbought: float = -20.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 Williams %R + Stochastic 信号。"""
        high_roll = price.rolling(window=wr_period).max()
        low_roll = price.rolling(window=wr_period).min()

        # Williams %R
        wr = -100 * (high_roll - price) / (high_roll - low_roll)

        # Stochastic %K and %D
        stoch_k_val = 100 * (price - low_roll) / (high_roll - low_roll)
        stoch_d_val = stoch_k_val.rolling(window=stoch_d).mean()

        # 趋势过滤
        ma = price.rolling(window=ma_period).mean()
        uptrend = price > ma

        # 入场：WR 超卖 + Stoch K 上穿 D + 趋势向上
        wr_oversold_cond = wr < wr_oversold
        stoch_cross_up = (stoch_k_val > stoch_d_val) & (stoch_k_val.shift(1) <= stoch_d_val.shift(1))
        entries = wr_oversold_cond & stoch_cross_up & uptrend

        # 出场：WR 超买 或 Stoch K 下穿 D
        wr_overbought_cond = (wr > wr_overbought) & (wr.shift(1) <= wr_overbought)
        stoch_cross_down = (stoch_k_val < stoch_d_val) & (stoch_k_val.shift(1) >= stoch_d_val.shift(1))
        exits = wr_overbought_cond | stoch_cross_down

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"WilliamsStoch | wr={wr_period} stoch_k={stoch_k} ma={ma_period} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def williams_stoch_signal(
    price: pd.Series,
    wr_period: int = 14,
    stoch_k: int = 14,
    ma_period: int = 100,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return WilliamsStochStrategy().generate_signals(price, wr_period=wr_period, stoch_k=stoch_k, ma_period=ma_period)
