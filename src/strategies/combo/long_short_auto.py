"""自动多空组合：牛市做多 + 熊市做空，全天候覆盖。

组合审计后的 Top 策略：
  做多：MinSwing v3（趋势向上时）
  做空：session_filter / trend_follow（趋势向下时，per-coin）
  横盘：不交易
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase
from src.strategies.minute_swing import MinuteSwingStrategy


def detect_trend(price: pd.Series, ma_period: int = 180) -> pd.Series:
    """检测趋势方向：UP / DOWN / FLAT。"""
    ma = price.rolling(window=ma_period).mean()
    ma_slope = (ma - ma.shift(24)) / ma.shift(24) * 100

    trend = pd.Series("FLAT", index=price.index)
    trend[(price > ma) & (ma_slope > 0.3)] = "UP"
    trend[(price < ma) & (ma_slope < -0.3)] = "DOWN"
    return trend


def short_signal_trend_follow(price, fast_ma=84, slow_ma=180, min_gap=288):
    """做空：双均线死叉 + MACD 死叉。"""
    sma_f = price.rolling(window=fast_ma).mean()
    sma_s = price.rolling(window=slow_ma).mean()
    death_cross = (sma_f < sma_s) & (sma_f.shift(1) >= sma_s.shift(1))

    e12 = price.ewm(span=12, adjust=False).mean()
    e26 = price.ewm(span=26, adjust=False).mean()
    macd = e12 - e26
    sig = macd.ewm(span=9, adjust=False).mean()
    macd_death = (macd < sig) & (macd.shift(1) >= sig.shift(1))

    raw = death_cross & macd_death
    entries = pd.Series(False, index=price.index)
    last = -min_gap * 2
    for i in range(len(raw)):
        if raw.iloc[i] and (i - last) >= min_gap:
            entries.iloc[i] = True
            last = i
    return entries.fillna(False)


def short_exit(price, fast_ma=84, slow_ma=180, trail_pct=1.0, stop_pct=3.0, tp_pct=12.0):
    """做空出场：trailing + 趋势反转 + 止损。"""
    sma_f = price.rolling(window=fast_ma).mean()
    sma_s = price.rolling(window=slow_ma).mean()
    # 做空用反转价格模拟
    inv = price.iloc[0] * 2 - price
    exits = pd.Series(False, index=price.index)
    return exits  # 由 combo 统一管理


class LongShortAutoStrategy(StrategyBase):
    """自动多空组合。"""

    @property
    def name(self) -> str:
        return "long_short_auto"

    def generate_signals(
        self,
        price: pd.Series,
        # 趋势检测
        trend_ma: int = 180,
        # 做多参数 (MinSwing)
        long_tp: float = 8.0,
        long_sl: float = 2.0,
        long_gap: int = 144,
        # 做空参数 (trend_follow)
        short_fast_ma: int = 84,
        short_slow_ma: int = 180,
        short_gap: int = 288,
        short_trail: float = 1.0,
        short_stop: float = 3.0,
        short_tp: float = 12.0,
        **kwargs,
    ) -> tuple[pd.Series, pd.Series]:
        """生成多空自动切换信号。"""
        trend = detect_trend(price, ma_period=trend_ma)
        ma = price.rolling(window=trend_ma).mean()

        # === 做多信号 (MinSwing) ===
        strat_long = MinuteSwingStrategy()
        e_long, x_long = strat_long.generate_signals(
            price, trend_ma=trend_ma, stop_pct=long_sl,
            take_profit_pct=long_tp, min_gap=long_gap
        )
        # 只在 UP 趋势做多
        e_long = e_long & (trend == "UP")

        # === 做空信号 (trend_follow) ===
        sma_f = price.rolling(window=short_fast_ma).mean()
        sma_s = price.rolling(window=short_slow_ma).mean()
        death_cross = (sma_f < sma_s) & (sma_f.shift(1) >= sma_s.shift(1))

        e12 = price.ewm(span=12, adjust=False).mean()
        e26 = price.ewm(span=26, adjust=False).mean()
        macd = e12 - e26
        sig = macd.ewm(span=9, adjust=False).mean()
        macd_death = (macd < sig) & (macd.shift(1) >= sig.shift(1))

        e_short_raw = death_cross & macd_death & (trend == "DOWN")

        # 做空限频
        e_short = pd.Series(False, index=price.index)
        last = -short_gap * 2
        for i in range(len(e_short_raw)):
            if e_short_raw.iloc[i] and (i - last) >= short_gap:
                e_short.iloc[i] = True
                last = i

        # === 合并信号 ===
        # 用反转价格模拟做空收益
        # vectorbt 只能做多，做空通过反转价格实现
        # 这里简单合并：做多和做空信号都作为"入场"
        entries = e_long | e_short

        # === 出场 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        entry_type = ""
        in_trade = False
        peak = 0.0
        trough = float('inf')

        for i in range(len(price)):
            if e_long.iloc[i] and not in_trade:
                entry_price = price.iloc[i]
                entry_type = "LONG"
                in_trade = True
                peak = entry_price
            elif e_short.iloc[i] and not in_trade:
                entry_price = price.iloc[i]
                entry_type = "SHORT"
                in_trade = True
                trough = entry_price
            elif in_trade:
                if entry_type == "LONG":
                    pnl = (price.iloc[i] - entry_price) / entry_price * 100
                    if price.iloc[i] > peak:
                        peak = price.iloc[i]
                    if pnl < -long_sl or pnl > long_tp or price.iloc[i] < ma.iloc[i]:
                        exits.iloc[i] = True
                        in_trade = False
                elif entry_type == "SHORT":
                    # 做空：价格下跌 = 盈利
                    pnl = (entry_price - price.iloc[i]) / entry_price * 100
                    if price.iloc[i] < trough:
                        trough = price.iloc[i]
                    trail_from_low = (price.iloc[i] - trough) / trough * 100
                    # 出场条件
                    if pnl < -short_stop:  # 做空止损（价格涨了）
                        exits.iloc[i] = True
                        in_trade = False
                    elif pnl > short_tp:  # 做空止盈
                        exits.iloc[i] = True
                        in_trade = False
                    elif trail_from_low > short_trail:  # trailing
                        exits.iloc[i] = True
                        in_trade = False
                    elif sma_f.iloc[i] > sma_s.iloc[i]:  # 趋势反转
                        exits.iloc[i] = True
                        in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        n_up = (trend == "UP").sum()
        n_down = (trend == "DOWN").sum()
        n_flat = (trend == "FLAT").sum()
        n_long = e_long.sum()
        n_short = e_short.sum()

        logger.debug(
            f"L/S Auto | UP:{n_up} DOWN:{n_down} FLAT:{n_flat} | "
            f"long:{n_long} short:{n_short} | total:{entries.sum()}"
        )
        return entries, exits


def long_short_auto_signal(price, **kwargs):
    return LongShortAutoStrategy().generate_signals(price, **kwargs)
