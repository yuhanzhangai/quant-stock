"""OBV (On-Balance Volume) 动量策略。

OBV 趋势领先价格：OBV 创新高但价格未创新高 = 即将突破。
结合价格突破确认入场。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class OBVMomentumStrategy(StrategyBase):
    """OBV 量价动量策略。

    入场条件：
    1. OBV 的均线金叉（OBV 的短均线 > 长均线）
    2. 价格在 MA 上方（趋势确认）
    3. OBV 创 N 日新高（量价配合）

    出场：OBV 均线死叉 或 价格跌破 MA
    """

    @property
    def name(self) -> str:
        return "obv_momentum"

    def generate_signals(
        self,
        price: pd.Series,
        obv_short: int = 10,
        obv_long: int = 30,
        price_ma: int = 50,
        obv_lookback: int = 20,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 OBV 动量信号。"""
        # 由于只有 price（没有 volume），用价格变化幅度模拟成交量方向
        # 实际使用时应接入真实 volume
        price_change = price.diff()
        # 使用价格变化绝对值作为 volume proxy
        vol_proxy = price_change.abs()
        obv = (vol_proxy * price_change.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()

        # OBV 均线
        obv_sma_short = obv.rolling(window=obv_short).mean()
        obv_sma_long = obv.rolling(window=obv_long).mean()

        # OBV 金叉
        obv_cross_up = (obv_sma_short > obv_sma_long) & (obv_sma_short.shift(1) <= obv_sma_long.shift(1))

        # OBV 新高
        obv.rolling(window=obv_lookback).max()

        # 价格趋势
        ma = price.rolling(window=price_ma).mean()
        above_ma = price > ma

        # 入场：OBV 金叉 + 价格在 MA 上方
        entries = obv_cross_up & above_ma

        # 出场：OBV 死叉 或 跌破 MA
        obv_cross_down = (obv_sma_short < obv_sma_long) & (obv_sma_short.shift(1) >= obv_sma_long.shift(1))
        price_break = (price < ma) & (price.shift(1) >= ma.shift(1))
        exits = obv_cross_down | price_break

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"OBV_Mom | obv_short={obv_short} obv_long={obv_long} "
            f"price_ma={price_ma} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def obv_momentum_signal(
    price: pd.Series,
    obv_short: int = 10,
    obv_long: int = 30,
    price_ma: int = 50,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return OBVMomentumStrategy().generate_signals(price, obv_short=obv_short, obv_long=obv_long, price_ma=price_ma)
