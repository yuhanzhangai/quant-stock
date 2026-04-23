"""策略路由器：根据市场情绪自动选择最优策略。

核心思路：
1. 检测当前市场状态（趋势强度、波动率、动量方向）
2. 根据状态选择历史上在该状态下表现最好的策略
3. 动态切换，不死守一个策略

市场状态分类：
- STRONG_TREND_UP: 强趋势上涨 -> AggressiveMom / TrendMA_Filtered
- WEAK_TREND_UP: 弱趋势上涨 -> Ichimoku / MultiTF
- RANGING: 横盘震荡 -> MeanRevBB / RSIExtreme
- VOLATILE_DOWN: 高波动下跌 -> 不交易 / 极度保守
- TREND_DOWN: 趋势下跌 -> 不交易
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


def classify_market(
    price: pd.Series,
    ma_period: int = 100,
    atr_period: int = 14,
    lookback: int = 50,
) -> pd.Series:
    """分类市场状态。

    Returns:
        Series of str: 市场状态标签
    """
    ma = price.rolling(window=ma_period).mean()
    ma_slope = (ma - ma.shift(20)) / ma.shift(20) * 100  # 20期变化率%

    # ATR 波动率
    high = price.rolling(2).max()
    low = price.rolling(2).min()
    prev_close = price.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=atr_period).mean()
    atr_pct = atr / price * 100  # ATR 占价格百分比
    atr_median = atr_pct.rolling(window=lookback).median()

    # 分类
    regime = pd.Series("RANGING", index=price.index)

    # 强趋势上涨：MA 斜率 > 2% 且价格在 MA 上方
    strong_up = (ma_slope > 2) & (price > ma)
    regime[strong_up] = "STRONG_TREND_UP"

    # 弱趋势上涨：MA 斜率 0-2% 且价格在 MA 上方
    weak_up = (ma_slope > 0) & (ma_slope <= 2) & (price > ma)
    regime[weak_up] = "WEAK_TREND_UP"

    # 趋势下跌：MA 斜率 < -1%
    down = ma_slope < -1
    regime[down] = "TREND_DOWN"

    # 高波动（覆盖其他状态）
    high_vol = atr_pct > atr_median * 1.8
    regime[high_vol & (ma_slope < 0)] = "VOLATILE_DOWN"

    return regime


class StrategyRouter(StrategyBase):
    """策略路由器。

    根据市场状态动态选择最优策略：
    - STRONG_TREND_UP -> AggressiveMomentum（追涨）
    - WEAK_TREND_UP -> MA 金叉（稳健趋势跟踪）
    - RANGING -> BB 均值回归（抄底反弹）
    - TREND_DOWN / VOLATILE_DOWN -> 不交易
    """

    @property
    def name(self) -> str:
        return "strategy_router"

    def generate_signals(
        self,
        price: pd.Series,
        ma_period: int = 100,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """根据市场状态路由到不同策略。"""
        regime = classify_market(price, ma_period=ma_period)

        # --- 各状态下的策略逻辑 ---

        # STRONG_TREND_UP: 追涨（创新高 + 连续上涨）
        rolling_high = price.rolling(window=30).max()
        new_high = price >= rolling_high
        up_bars = (price > price.shift(1)).rolling(window=3).sum() >= 3
        strong_entry = new_high & up_bars

        # WEAK_TREND_UP: MA 金叉
        sma20 = price.rolling(window=20).mean()
        sma50 = price.rolling(window=50).mean()
        weak_entry = (sma20 > sma50) & (sma20.shift(1) <= sma50.shift(1))

        # RANGING: BB 下轨反弹
        bb_mid = price.rolling(window=20).mean()
        bb_std = price.rolling(window=20).std()
        bb_lower = bb_mid - 2.0 * bb_std
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=14).mean()
        losses = (-delta).clip(lower=0).rolling(window=14).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        range_entry = (price < bb_lower) & (rsi < 30)
        range_entry = range_entry & (~range_entry.shift(1).fillna(False))

        # --- 路由 ---
        is_strong = regime == "STRONG_TREND_UP"
        is_weak = regime == "WEAK_TREND_UP"
        is_range = regime == "RANGING"
        is_down = (regime == "TREND_DOWN") | (regime == "VOLATILE_DOWN")

        entries = (
            (strong_entry & is_strong)
            | (weak_entry & is_weak)
            | (range_entry & is_range)
        )

        # 出场逻辑
        # 趋势策略：跌破 MA 出场
        trend_exit = (price < sma50) & (price.shift(1) >= sma50.shift(1))
        # 均值回归：回到中轨出场
        range_exit = (price > bb_mid) & (price.shift(1) <= bb_mid.shift(1))
        # 进入下跌状态强制出场
        regime_exit = is_down & (~is_down.shift(1).fillna(False))

        exits = trend_exit | range_exit | regime_exit

        entries = entries & (~entries.shift(1).fillna(False))
        exits = exits & (~exits.shift(1).fillna(False))
        entries = entries.fillna(False)
        exits = exits.fillna(False)

        # 统计
        regime_counts = regime.value_counts()
        logger.debug(
            f"Router | 状态分布: {dict(regime_counts)} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def strategy_router_signal(
    price: pd.Series, ma_period: int = 100, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return StrategyRouter().generate_signals(price, ma_period=ma_period)
