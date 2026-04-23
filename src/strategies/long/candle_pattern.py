"""K 线形态策略：吞没形态 + 趋势确认。

看涨吞没：大阳线完全包覆前一根阴线 = 强反转信号。
在趋势回调中出现看涨吞没 = 高胜率入场。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class CandlePatternStrategy(StrategyBase):
    """K 线形态 + 趋势策略。

    入场：趋势向上 + 回调后出现看涨吞没形态
    出场：止盈/止损/趋势反转
    """

    @property
    def name(self) -> str:
        return "candle_pattern"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 200,
        body_ratio: float = 1.5,   # 当前 K 线实体 > 前一根的 1.5 倍
        pullback_bars: int = 12,   # 回调持续至少 12 根
        min_gap: int = 48,
        stop_pct: float = 2.0,
        take_profit_pct: float = 4.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 K 线形态信号。"""
        ma = price.rolling(window=trend_ma).mean()
        uptrend = price > ma

        # 简化"吞没"：当前涨幅 > 前一根跌幅的 body_ratio 倍
        current_change = price - price.shift(1)
        prev_change = price.shift(1) - price.shift(2)

        # 看涨吞没：前一根跌，当前涨，且当前涨幅更大
        bullish_engulf = (prev_change < 0) & (current_change > 0) & \
                         (current_change > (-prev_change) * body_ratio)

        # 回调确认：最近 N 根有跌（不是一路涨中的正常 K 线）
        recent_low = price.rolling(window=pullback_bars).min()
        had_pullback = (price - recent_low) / recent_low > 0.005  # 至少从低点反弹 0.5%

        raw_entries = uptrend & bullish_engulf & had_pullback

        # 限频
        entries = pd.Series(False, index=price.index)
        last = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last) >= min_gap:
                entries.iloc[i] = True
                last = i

        # 出场
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False
        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                pnl = (price.iloc[i] - entry_price) / entry_price * 100
                if pnl < -stop_pct or pnl > take_profit_pct:
                    exits.iloc[i] = True
                    in_trade = False
                elif price.iloc[i] < ma.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"CandlePattern | engulf:{bullish_engulf.sum()} pullback:{had_pullback.sum()} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def candle_pattern_signal(
    price: pd.Series, trend_ma: int = 200, body_ratio: float = 1.5,
    min_gap: int = 48, stop_pct: float = 2.0, take_profit_pct: float = 4.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return CandlePatternStrategy().generate_signals(
        price, trend_ma=trend_ma, body_ratio=body_ratio, min_gap=min_gap,
        stop_pct=stop_pct, take_profit_pct=take_profit_pct
    )
