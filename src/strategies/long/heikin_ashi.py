"""Heikin-Ashi 趋势策略：平滑 K 线过滤噪音。

Heikin-Ashi 将 OHLC 平滑处理，连续同色 K 线 = 强趋势。
适合趋势跟踪，减少假信号。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


def calc_heikin_ashi(price: pd.Series) -> tuple[pd.Series, pd.Series]:
    """计算 Heikin-Ashi 收盘价和方向。

    简化版：只用收盘价模拟 HA。
    HA_Close ≈ (Open + High + Low + Close) / 4 ≈ price 的 2 期均值
    HA 方向：HA_Close > HA_Open = 阳线
    """
    ha_close = price.rolling(window=2).mean()
    ha_open = ha_close.shift(1)
    ha_bullish = ha_close > ha_open
    return ha_close, ha_bullish


class HeikinAshiTrendStrategy(StrategyBase):
    """Heikin-Ashi 趋势策略。

    入场：连续 N 根 HA 阳线 + 价格在 MA 上方
    出场：出现 HA 阴线 或 跌破 MA
    """

    @property
    def name(self) -> str:
        return "heikin_ashi"

    def generate_signals(
        self,
        price: pd.Series,
        consec_bullish: int = 3,
        ma_period: int = 50,
        exit_consec_bearish: int = 2,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 HA 趋势信号。"""
        _, ha_bullish = calc_heikin_ashi(price)

        # 连续阳线
        bull_count = ha_bullish.astype(int).rolling(window=consec_bullish).sum()
        consec_bull = bull_count >= consec_bullish

        # 趋势过滤
        ma = price.rolling(window=ma_period).mean()
        above_ma = price > ma

        # 入场：首次达到连续阳线 + MA 上方
        entries = consec_bull & above_ma
        entries = entries & (~entries.shift(1).fillna(False))

        # 出场：连续阴线
        bear_count = (~ha_bullish).astype(int).rolling(window=exit_consec_bearish).sum()
        exits = bear_count >= exit_consec_bearish
        exits = exits & (~exits.shift(1).fillna(False))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"HeikinAshi | consec={consec_bullish} ma={ma_period} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def heikin_ashi_signal(
    price: pd.Series, consec_bullish: int = 3, ma_period: int = 50,
    exit_consec_bearish: int = 2, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return HeikinAshiTrendStrategy().generate_signals(
        price, consec_bullish=consec_bullish, ma_period=ma_period,
        exit_consec_bearish=exit_consec_bearish
    )
