"""自适应策略：根据市场状态自动切换趋势跟踪/均值回归。

核心思路：
- 检测当前市场状态（趋势/震荡/高波动）
- 趋势市 -> 用 TrendMA_Filtered 逻辑
- 震荡市 -> 用 MeanRevBB 逻辑
- 高波动 -> 减少交易（提高入场门槛）
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


def detect_regime(price: pd.Series, atr_period: int = 14, lookback: int = 50) -> pd.Series:
    """检测市场状态。

    返回:
        Series of str: "trending" / "ranging" / "volatile"

    方法：
    - ADX > 25 或均线斜率明显 -> trending
    - BB 宽度 < 中位数 -> ranging
    - ATR > 1.5 * 中位数 -> volatile
    """
    # ATR
    high = price.rolling(2).max()
    low = price.rolling(2).min()
    prev_close = price.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=atr_period).mean()
    atr_median = atr.rolling(window=lookback).median()

    # BB 宽度
    bb_mid = price.rolling(window=20).mean()
    bb_std = price.rolling(window=20).std()
    bb_width = (2 * bb_std) / bb_mid
    bb_width_median = bb_width.rolling(window=lookback).median()

    # 趋势强度: 用均线斜率
    ma50 = price.rolling(window=50).mean()
    ma_slope = (ma50 - ma50.shift(10)) / ma50.shift(10)
    trending = ma_slope.abs() > 0.02  # 50MA 10期变化 > 2%

    volatile = atr > atr_median * 1.5
    ranging = bb_width < bb_width_median * 0.8

    regime = pd.Series("ranging", index=price.index)
    regime[trending] = "trending"
    regime[volatile] = "volatile"

    return regime


class AdaptiveStrategy(StrategyBase):
    """自适应策略。

    趋势市: 双均线过滤入场（追趋势）
    震荡市: BB 下轨反弹入场（抄底）
    高波动: 不交易（避险）
    """

    @property
    def name(self) -> str:
        return "adaptive"

    def generate_signals(
        self,
        price: pd.Series,
        # 趋势参数
        short_ma: int = 20,
        long_ma: int = 100,
        # 均值回归参数
        bb_period: int = 20,
        bb_std: float = 2.0,
        # RSI
        rsi_period: int = 14,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成自适应信号。"""
        regime = detect_regime(price)

        # --- 趋势模式信号 ---
        sma_short = price.rolling(window=short_ma).mean()
        sma_long = price.rolling(window=long_ma).mean()
        trend_entry = (sma_short > sma_long) & (sma_short.shift(1) <= sma_long.shift(1))
        trend_exit = (sma_short < sma_long) & (sma_short.shift(1) >= sma_long.shift(1))

        # --- 均值回归信号 ---
        bb_mid = price.rolling(window=bb_period).mean()
        bb_s = price.rolling(window=bb_period).std()
        bb_lower = bb_mid - bb_std * bb_s
        mr_entry = (price < bb_lower) & (price.shift(1) >= bb_lower.shift(1))

        # RSI 确认
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        mr_entry = mr_entry & (rsi < 35)
        mr_exit = (price > bb_mid) & (price.shift(1) <= bb_mid.shift(1))

        # --- 组合 ---
        is_trending = regime == "trending"
        is_ranging = regime == "ranging"
        is_volatile = regime == "volatile"

        entries = (trend_entry & is_trending) | (mr_entry & is_ranging)
        exits = (trend_exit & is_trending) | (mr_exit & is_ranging) | is_volatile

        # 边沿触发
        entries = entries & (~entries.shift(1).fillna(False))
        exits = exits & (~exits.shift(1).fillna(False))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        # 状态统计
        n_trend = is_trending.sum()
        n_range = is_ranging.sum()
        n_vol = is_volatile.sum()
        logger.debug(
            f"Adaptive | trend:{n_trend} range:{n_range} vol:{n_vol} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def adaptive_signal(
    price: pd.Series, short_ma: int = 20, long_ma: int = 100,
    bb_period: int = 20, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return AdaptiveStrategy().generate_signals(
        price, short_ma=short_ma, long_ma=long_ma, bb_period=bb_period
    )
