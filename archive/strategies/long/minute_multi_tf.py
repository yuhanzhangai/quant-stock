"""分钟线多周期策略：4h 趋势方向 + 5m 精确入场。

解决 5m 不稳定的核心方法：
用已验证的 4h 级别判断是否应该交易（regime filter），
只在 4h 确认多头时，才用 5m 信号做精确入场。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MinuteMultiTFStrategy(StrategyBase):
    """4h 趋势 + 5m 入场。

    4h 层（模拟）：
    - 价格 > MA(50)（约 200 根 5m）且斜率向上 = 允许做多
    - 否则 = 不交易

    5m 层：
    - EMA(12) 上穿 EMA(26) + RSI < 60 = 入场
    - EMA(12) 下穿 EMA(26) 或 止损 = 出场
    """

    @property
    def name(self) -> str:
        return "minute_multi_tf"

    def generate_signals(
        self,
        price: pd.Series,
        # 4h 级别（用 5m bars 模拟）
        h4_ma_bars: int = 200,  # 200*5m ≈ 17h (接近 4h*4)
        h4_slope_check: int = 48,  # 48*5m = 4h 检查斜率
        # 5m 入场
        ema_fast: int = 12,
        ema_slow: int = 26,
        rsi_period: int = 14,
        rsi_max: int = 60,  # RSI 不超买才入场
        # 风控
        stop_pct: float = 1.5,
        take_profit_pct: float = 3.0,
        min_gap: int = 48,  # 48*5m = 4h 间隔
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成多周期信号。"""
        # === 4h 级别趋势过滤 ===
        ma_4h = price.rolling(window=h4_ma_bars).mean()
        ma_slope = (ma_4h - ma_4h.shift(h4_slope_check)) / ma_4h.shift(h4_slope_check)
        allow_long = (price > ma_4h) & (ma_slope > 0)

        # === 5m 入场 ===
        ema_f = price.ewm(span=ema_fast, adjust=False).mean()
        ema_s = price.ewm(span=ema_slow, adjust=False).mean()
        ema_cross = (ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))

        # RSI
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        rsi_ok = rsi < rsi_max

        raw_entries = allow_long & ema_cross & rsi_ok

        # 限频
        entries = pd.Series(False, index=price.index)
        last = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last) >= min_gap:
                entries.iloc[i] = True
                last = i

        # === 出场：止损/止盈/EMA 死叉 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False
        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                pnl = (price.iloc[i] - entry_price) / entry_price * 100
                ema_death = (
                    (ema_f.iloc[i] < ema_s.iloc[i]) and (ema_f.iloc[i - 1] >= ema_s.iloc[i - 1]) if i > 0 else False
                )
                if pnl < -stop_pct or pnl > take_profit_pct or ema_death:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        n_allowed = allow_long.sum()
        logger.debug(
            f"MinMultiTF | 4h允许做多:{n_allowed}/{len(price)} bars | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def minute_multi_tf_signal(
    price: pd.Series,
    h4_ma_bars: int = 200,
    stop_pct: float = 1.5,
    take_profit_pct: float = 3.0,
    min_gap: int = 48,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MinuteMultiTFStrategy().generate_signals(
        price, h4_ma_bars=h4_ma_bars, stop_pct=stop_pct, take_profit_pct=take_profit_pct, min_gap=min_gap
    )
