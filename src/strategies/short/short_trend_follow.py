"""
Strategy Status: Candidate
Strategy Name: short_trend_follow
Strategy Version: 1.0.0
Research State: Needs full validation pipeline
Allowed Changes:
- bug fix
- logging
Not Allowed:
- silent parameter changes
- unrecorded optimization

趋势跟空策略：MA 死叉 + MACD 死叉双重确认做空。

设计理念：
- 不猜顶/不抄底，只在趋势确认后跟随
- 双均线死叉（短MA < 长MA）确认中期趋势转空
- MACD 死叉确认短期动量向下
- 最保守的做空策略，但信号质量高

信号逻辑：
  入场 = 短MA < 长MA（死叉）+ MACD < Signal + 价格低于两条MA
  出场 = 短MA > 长MA（金叉）+ 固定止损/止盈

特点：信号少但准确率高，适合强趋势行情
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ShortTrendFollowStrategy(StrategyBase):
    """趋势跟空策略。

    双重确认机制：
    1. 均线死叉：短期 MA 下穿长期 MA → 中期趋势转空
    2. MACD 死叉：MACD 线下穿信号线 → 短期动量确认
    3. 价格在两条 MA 下方 → 空头排列确认
    """

    @property
    def name(self) -> str:
        return "short_trend_follow"

    def generate_signals(
        self,
        price: pd.Series,
        fast_ma: int = 60,                 # 短期 MA（60*5m=5h）
        slow_ma: int = 180,                # 长期 MA（180*5m=15h）
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        min_gap: int = 288,                # 最少间隔（288*5m=24h，保守）
        stop_pct: float = 3.0,             # 止损宽一些（趋势策略需要给空间）
        take_profit_pct: float = 10.0,     # 止盈大（趋势跟踪追求大利润）
        trail_pct: float = 2.0,            # trailing stop
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成趋势跟空信号。"""
        n = len(price)

        # === 均线系统 ===
        ma_fast = price.rolling(window=fast_ma).mean()
        ma_slow = price.rolling(window=slow_ma).mean()

        # 空头排列：短MA < 长MA 且两条MA都向下
        bearish_cross = (ma_fast < ma_slow)
        ma_fast_slope = ma_fast - ma_fast.shift(10)
        ma_slow_slope = ma_slow - ma_slow.shift(10)
        both_declining = (ma_fast_slope < 0) & (ma_slow_slope < 0)

        # 价格在两条 MA 下方
        price_below_both = (price < ma_fast) & (price < ma_slow)

        # === MACD 死叉 ===
        ema_fast = price.ewm(span=macd_fast, adjust=False).mean()
        ema_slow = price.ewm(span=macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=macd_signal, adjust=False).mean()
        macd_hist = macd_line - signal_line

        # MACD 在零轴下方且死叉
        macd_bearish = (macd_line < 0) & (macd_line < signal_line)
        macd_death_cross = (
            (macd_line < signal_line) &
            (macd_line.shift(1) >= signal_line.shift(1))
        )

        # === 入场：空头排列 + MA 下行 + MACD 确认 ===
        raw_entries = bearish_cross & both_declining & price_below_both & macd_death_cross

        # 限制频率
        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场：trailing + 趋势反转 + 固定止损 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        trough = 0.0
        in_trade = False

        for i in range(n):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                trough = entry_price
                in_trade = True
            elif in_trade and entry_price > 0:
                if price.iloc[i] < trough:
                    trough = price.iloc[i]

                price_change = (price.iloc[i] - entry_price) / entry_price * 100
                bounce = (price.iloc[i] - trough) / trough * 100 if trough > 0 else 0

                # 止损
                if price_change > stop_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # 止盈
                elif price_change < -take_profit_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # Trailing stop：趋势中利润回撤
                elif bounce > trail_pct and price_change < -2.0:
                    exits.iloc[i] = True
                    in_trade = False
                # 均线金叉（趋势反转）
                elif ma_fast.iloc[i] > ma_slow.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortTrendFollow | fast_ma={fast_ma} slow_ma={slow_ma} "
            f"gap={min_gap} stop={stop_pct}% tp={take_profit_pct}% | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def short_trend_follow_signal(
    price: pd.Series,
    fast_ma: int = 60,
    slow_ma: int = 180,
    min_gap: int = 288,
    stop_pct: float = 3.0,
    take_profit_pct: float = 10.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数。"""
    return ShortTrendFollowStrategy().generate_signals(
        price,
        fast_ma=fast_ma,
        slow_ma=slow_ma,
        min_gap=min_gap,
        stop_pct=stop_pct,
        take_profit_pct=take_profit_pct,
        **kwargs,
    )
