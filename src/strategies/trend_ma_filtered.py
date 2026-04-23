"""带 ATR 过滤 + RSI 确认的改进双均线策略。"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class TrendMAFilteredStrategy(StrategyBase):
    """改进双均线策略。

    在基础金叉/死叉信号上增加：
    1. ATR 波动过滤：只在波动率足够大时开仓（避免震荡市假信号）
    2. RSI 确认：金叉时 RSI 不能超买（<70），死叉时 RSI 不能超卖（>30）
    3. 趋势强度：要求短均线与长均线之差超过 ATR 的一定比例
    """

    @property
    def name(self) -> str:
        return "trend_ma_filtered"

    def generate_signals(
        self,
        price: pd.Series,
        short_window: int = 20,
        long_window: int = 100,
        atr_period: int = 14,
        rsi_period: int = 14,
        atr_mult: float = 0.5,
        rsi_upper: int = 70,
        rsi_lower: int = 30,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成过滤后的交易信号。"""
        short_ma = price.rolling(window=short_window).mean()
        long_ma = price.rolling(window=long_window).mean()

        # ATR 计算
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # RSI 计算
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # 基础金叉/死叉
        cross_up = (short_ma > long_ma) & (short_ma.shift(1) <= long_ma.shift(1))
        cross_down = (short_ma < long_ma) & (short_ma.shift(1) >= long_ma.shift(1))

        # 趋势过滤：价格在长均线上方且 ATR 不太低（过滤极低波动横盘）
        atr_median = atr.rolling(window=50).median()
        vol_ok = atr > atr_median * atr_mult

        # RSI 过滤
        rsi_ok_buy = rsi < rsi_upper
        rsi_ok_sell = rsi > rsi_lower

        # 最终信号
        entries = cross_up & vol_ok & rsi_ok_buy
        exits = cross_down & rsi_ok_sell

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"TrendMA_Filtered | short={short_window} long={long_window} "
            f"atr_mult={atr_mult} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def trend_ma_filtered_signal(
    price: pd.Series,
    short_window: int = 20,
    long_window: int = 100,
    atr_mult: float = 0.5,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """独立函数版本，用于网格搜索。"""
    strategy = TrendMAFilteredStrategy()
    return strategy.generate_signals(
        price, short_window=short_window, long_window=long_window, atr_mult=atr_mult
    )
