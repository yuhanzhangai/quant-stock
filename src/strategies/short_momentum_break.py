"""做空动量崩溃策略：捕捉加速下跌的动量突破。

设计理念：
- 熊市中，跌破关键支撑后价格往往加速下跌（恐慌性抛售）
- 利用 Donchian 通道下轨突破 + 动量加速确认
- 比 short_swing 更激进：追跌而非等反弹

信号逻辑：
  入场 = 跌破 N 根低点 + ROC 加速为负 + 下降趋势确认
  出场 = trailing stop(价格从低点反弹 X%）+ RSI 极度超卖反弹 + 止损

适用场景：剧烈下跌、恐慌抛售、趋势加速期
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ShortMomentumBreakStrategy(StrategyBase):
    """做空动量崩溃策略。

    核心机制：
    1. 趋势确认：价格 < MA 且 MA 下行
    2. 动量突破：价格跌破 N 根 K 线最低价（Donchian 下轨）
    3. 加速确认：ROC（变化率）为负且加速（ROC 的 ROC 也为负）
    4. 出场：trailing stop 从低点反弹、或 RSI 极度超卖后反弹

    用反转价格技巧让 vectorbt 做多模拟做空。
    """

    @property
    def name(self) -> str:
        return "short_momentum_break"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 120,              # 趋势 MA（比 short_swing 的 180 更短，更激进）
        breakdown_window: int = 36,        # Donchian 下轨周期（36*5m=3h）
        roc_period: int = 24,             # ROC 周期（24*5m=2h）
        rsi_period: int = 14,
        rsi_extreme_low: int = 20,        # RSI 极度超卖阈值（出场用）
        min_gap: int = 144,               # 最少间隔
        stop_pct: float = 2.5,            # 止损：价格上涨 2.5%
        trail_pct: float = 1.5,           # trailing stop：从低点反弹 1.5% 止盈
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """在原始价格上生成做空动量崩溃信号。"""
        n = len(price)

        # === 趋势层：下降趋势 ===
        ma = price.rolling(window=trend_ma).mean()
        ma_slope = ma - ma.shift(10)
        downtrend = (price < ma) & (ma_slope < 0)

        # === Donchian 下轨突破 ===
        donchian_low = price.rolling(window=breakdown_window).min()
        # 价格跌破最近 N 根的最低价（新低突破）
        breakdown = (price < donchian_low.shift(1)) & (price.shift(1) >= donchian_low.shift(2))

        # === ROC 加速 ===
        roc = (price - price.shift(roc_period)) / price.shift(roc_period) * 100
        roc_accel = roc - roc.shift(roc_period // 2)  # ROC 的变化（加速度）
        momentum_accel = (roc < 0) & (roc_accel < 0)  # ROC 为负且在加速

        # === RSI ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # === 入场：下降趋势 + 动量突破 + 加速确认 ===
        raw_entries = downtrend & breakdown & momentum_accel

        # 限制频率
        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场逻辑 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        trough = 0.0  # 持仓期间的最低价
        in_trade = False

        for i in range(n):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                trough = entry_price
                in_trade = True
            elif in_trade and entry_price > 0:
                # 更新最低价
                if price.iloc[i] < trough:
                    trough = price.iloc[i]

                price_change = (price.iloc[i] - entry_price) / entry_price * 100
                bounce_from_low = (price.iloc[i] - trough) / trough * 100 if trough > 0 else 0

                # 止损：价格上涨超过 stop_pct（做空亏损）
                if price_change > stop_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # Trailing stop：从最低点反弹超过 trail_pct（锁定利润）
                elif bounce_from_low > trail_pct and price_change < 0:
                    exits.iloc[i] = True
                    in_trade = False
                # RSI 极度超卖后反弹（市场可能触底）
                elif i > 0 and rsi.iloc[i] > rsi_extreme_low and rsi.iloc[i - 1] <= rsi_extreme_low:
                    exits.iloc[i] = True
                    in_trade = False
                # 趋势反转
                elif price.iloc[i] > ma.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortMomentumBreak | trend_ma={trend_ma} breakdown={breakdown_window} "
            f"roc={roc_period} trail={trail_pct}% stop={stop_pct}% | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def short_momentum_break_signal(
    price: pd.Series,
    trend_ma: int = 120,
    breakdown_window: int = 36,
    min_gap: int = 144,
    stop_pct: float = 2.5,
    trail_pct: float = 1.5,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数：生成做空动量崩溃信号。"""
    return ShortMomentumBreakStrategy().generate_signals(
        price,
        trend_ma=trend_ma,
        breakdown_window=breakdown_window,
        min_gap=min_gap,
        stop_pct=stop_pct,
        trail_pct=trail_pct,
        **kwargs,
    )
