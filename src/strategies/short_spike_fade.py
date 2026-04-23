"""急涨回落做空策略：下降趋势中的异常反弹做空。

设计理念：
- 下降趋势中偶尔出现急涨（空头回补、假突破、消息刺激）
- 这些急涨往往是做空的绝佳机会（均值回归）
- 类似 ExtremeReversal 的反向版本

信号逻辑：
  入场 = 下降趋势 + 短期急涨（N根涨幅>X%）+ 价格仍在MA下方
  出场 = trailing stop + 固定止盈/止损

特点：
- 入场时机精准（反弹高点附近入场）
- 天然止损点小（急涨高点就是止损位）
- 与 trend_follow（追趋势）互补
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ShortSpikeFadeStrategy(StrategyBase):
    """急涨回落做空策略。

    核心机制：
    1. 大趋势下行（MA 下行）
    2. 检测短期急涨（N根K线内涨幅超过阈值）
    3. 急涨后出现收阴K线（反弹结束信号）
    4. 入场做空，止损设在急涨高点上方
    """

    @property
    def name(self) -> str:
        return "short_spike_fade"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,               # 趋势 MA 周期
        spike_window: int = 6,             # 急涨检测窗口（6*5m=30分钟）
        spike_pct: float = 2.0,            # 急涨阈值（30分钟内涨2%）
        confirm_bars: int = 2,             # 急涨后等 N 根收阴确认反弹结束
        rsi_period: int = 14,
        min_gap: int = 192,
        stop_pct: float = 1.5,             # 紧止损（急涨高点附近）
        take_profit_pct: float = 4.0,      # 中等止盈
        trail_pct: float = 1.0,            # 紧 trailing
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成急涨回落做空信号。"""
        n = len(price)

        # === 趋势层 ===
        ma = price.rolling(window=trend_ma).mean()
        ma_slope = ma - ma.shift(20)
        downtrend = (price < ma) & (ma_slope < 0)

        # === 检测急涨 ===
        # 过去 spike_window 根内的涨幅
        price_change = (price - price.shift(spike_window)) / price.shift(spike_window) * 100
        had_spike = price_change > spike_pct

        # 最近 spike_window*2 内有过急涨（允许延迟确认）
        recent_spike = had_spike.rolling(window=spike_window * 2).max().fillna(0).astype(bool)

        # === 急涨后收阴（反弹结束确认）===
        bearish_bar = price < price.shift(1)
        consecutive_bearish = bearish_bar.rolling(window=confirm_bars).sum() >= confirm_bars

        # === RSI 不在极端超卖（还有下跌空间）===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        not_oversold = rsi > 30  # RSI > 30 才做空（太低了可能到底了）

        # === 入场 ===
        raw_entries = downtrend & recent_spike & consecutive_bearish & not_oversold

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

                price_change_pct = (price.iloc[i] - entry_price) / entry_price * 100
                bounce = (price.iloc[i] - trough) / trough * 100 if trough > 0 else 0

                # 止损
                if price_change_pct > stop_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # 止盈
                elif price_change_pct < -take_profit_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # Trailing stop
                elif bounce > trail_pct and price_change_pct < -0.5:
                    exits.iloc[i] = True
                    in_trade = False
                # 趋势反转
                elif price.iloc[i] > ma.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortSpikeFade | spike_win={spike_window} spike_pct={spike_pct}% "
            f"confirm={confirm_bars} gap={min_gap} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def short_spike_fade_signal(
    price: pd.Series,
    spike_pct: float = 2.0,
    min_gap: int = 192,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数。"""
    return ShortSpikeFadeStrategy().generate_signals(
        price, spike_pct=spike_pct, min_gap=min_gap, **kwargs,
    )
