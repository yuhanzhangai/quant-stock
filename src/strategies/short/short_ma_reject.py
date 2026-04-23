"""均线拒绝做空策略：价格反弹测试下行均线被拒绝后做空。

设计理念：
- 在下降趋势中，价格反弹到均线附近经常被"拒绝"（无法站上）
- 这是经典的"Test & Reject"形态，技术面做空信号之王
- 比 bounce_fade 更宽松的入场条件（解决信号太少的问题）
- 比 momentum_break 更安全（不追跌，而是等反弹）

信号逻辑：
  入场 = 下降趋势 + 价格从下方接近MA（反弹）+ K线收阴（被拒绝）
  出场 = trailing stop + 固定止损 + 趋势反转

核心改进：
- 用"价格接近MA的速度"代替严格的距离阈值
- 加入连续K线判断（最近3根有上涨但当前根收阴=反弹失败）
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ShortMARejectStrategy(StrategyBase):
    """均线拒绝做空策略。

    入场三要素：
    1. 大趋势向下（MA 下行 + 价格在 MA 下方）
    2. 近期有反弹（过去 N 根内价格曾接近 MA）
    3. 反弹被拒绝（当前 K 线收阴 + 价格开始远离 MA）
    """

    @property
    def name(self) -> str:
        return "short_ma_reject"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,               # 趋势 MA 周期
        approach_window: int = 12,          # 回看 N 根判断是否有反弹接近 MA
        approach_pct: float = 0.5,          # 接近 MA 的阈值（距离 < 0.5%）
        reject_bars: int = 3,              # 连续 N 根收阴确认拒绝
        rsi_period: int = 14,
        rsi_max: int = 55,                 # RSI 不能太高（不做超买做空，那是 rsi_overbought）
        min_gap: int = 192,
        stop_pct: float = 2.0,
        take_profit_pct: float = 6.0,
        trail_pct: float = 1.5,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成均线拒绝做空信号。"""
        n = len(price)

        # === 趋势层 ===
        ma = price.rolling(window=trend_ma).mean()
        ma_slope = ma - ma.shift(20)
        downtrend = (price < ma) & (ma_slope < 0)

        # === 价格接近 MA（反弹到阻力位）===
        distance = (ma - price) / ma * 100  # 正值=价格在 MA 下方的距离百分比
        # 最近 approach_window 根内有 K 线接近过 MA
        min_dist = distance.rolling(window=approach_window).min()
        had_approach = min_dist < approach_pct

        # === 反弹被拒绝（连续收阴）===
        bearish_bar = price < price.shift(1)  # 当前收阴
        # 最近 reject_bars 根中大部分收阴
        bearish_count = bearish_bar.rolling(window=reject_bars).sum()
        rejection = bearish_count >= reject_bars - 1  # 至少 N-1 根收阴

        # === 价格正在远离 MA（拒绝确认）===
        moving_away = distance > distance.shift(1)  # 距离在扩大

        # === RSI ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        rsi_ok = rsi < rsi_max  # RSI 不太高

        # === 入场 ===
        raw_entries = downtrend & had_approach & rejection & moving_away & rsi_ok

        # 限制频率
        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场 ===
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
                # Trailing stop
                elif bounce > trail_pct and price_change < -1.0:
                    exits.iloc[i] = True
                    in_trade = False
                # 趋势反转
                elif price.iloc[i] > ma.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortMAReject | trend_ma={trend_ma} approach={approach_pct}% "
            f"reject_bars={reject_bars} gap={min_gap} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def short_ma_reject_signal(
    price: pd.Series,
    trend_ma: int = 180,
    min_gap: int = 192,
    stop_pct: float = 2.0,
    take_profit_pct: float = 6.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数。"""
    return ShortMARejectStrategy().generate_signals(
        price,
        trend_ma=trend_ma,
        min_gap=min_gap,
        stop_pct=stop_pct,
        take_profit_pct=take_profit_pct,
        **kwargs,
    )
