"""分钟线波段交易：大级别方向 + 小级别择时 + 杠杆止损。

设计目标：
- 5m/15m K 线级别
- 每月 5-15 笔交易
- 持仓几小时到 1-2 天
- 适合 3-5x 杠杆
- 严格止损（2-3% 本金）
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MinuteSwingStrategy(StrategyBase):
    """分钟线波段交易。

    大级别：用 4h 级别 MA（240 根 5m = 20h）判断趋势
    中级别：用 1h 级别 MA（12 根 5m）判断短趋势
    入场：短趋势回调到支撑 + RSI 从超卖回升 + MACD 金叉
    出场：利润目标 或 止损 或 趋势反转
    """

    @property
    def name(self) -> str:
        return "minute_swing"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 240,  # 大趋势（240*5m=20h）
        mid_ma: int = 60,  # 中趋势（60*5m=5h）
        fast_ma: int = 12,  # 快线（12*5m=1h）
        rsi_period: int = 14,
        rsi_entry: int = 40,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        min_gap: int = 72,  # 最少间隔（72*5m=6h）
        stop_pct: float = 2.0,  # 止损 2%
        take_profit_pct: float = 4.0,  # 止盈 4%（盈亏比 2:1）
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成分钟线波段信号。"""
        # === 趋势层 ===
        ma_trend = price.rolling(window=trend_ma).mean()
        price.rolling(window=mid_ma).mean()
        price.rolling(window=fast_ma).mean()

        uptrend = (price > ma_trend) & (ma_trend > ma_trend.shift(20))

        # === RSI ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # RSI 从超卖回升
        rsi_bounce = (rsi > rsi_entry) & (rsi.shift(1) <= rsi_entry)

        # === MACD ===
        ema_fast = price.ewm(span=macd_fast, adjust=False).mean()
        ema_slow = price.ewm(span=macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=macd_signal, adjust=False).mean()
        macd_cross = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))

        # === 入场：大趋势 + (RSI回升 或 MACD金叉) ===
        raw_entries = uptrend & (rsi_bounce | macd_cross)

        # 限制频率
        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场：止损/止盈/趋势反转 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False

        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                pnl_pct = (price.iloc[i] - entry_price) / entry_price * 100
                # 止损
                if pnl_pct < -stop_pct or pnl_pct > take_profit_pct or price.iloc[i] < ma_trend.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MinSwing | trend={trend_ma} mid={mid_ma} fast={fast_ma} "
            f"stop={stop_pct}% tp={take_profit_pct}% | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def minute_swing_signal(
    price: pd.Series,
    trend_ma: int = 240,
    stop_pct: float = 2.0,
    take_profit_pct: float = 4.0,
    min_gap: int = 72,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MinuteSwingStrategy().generate_signals(
        price, trend_ma=trend_ma, stop_pct=stop_pct, take_profit_pct=take_profit_pct, min_gap=min_gap
    )
