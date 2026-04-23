"""波动率收缩后爆发策略（5m 级别）。

核心逻辑：
- 检测波动率收缩：ATR(14) < ATR(14) 的 50 期中位数 * 0.7
- 收缩持续至少 24 根 5m（2h）后，价格向上突破收缩区间高点 = 入场
- 出场：ATR 扩大到 > 中位数 * 1.5 或 止损 2%
- 趋势过滤：价格 > MA(200)
- min_gap 限频：96 根（8h）
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase


class VolSqueeze5mStrategy(StrategyBase):
    """波动率收缩后爆发策略。

    ATR 持续低于中位数 * 0.7 达到 24 根 5m K 线后，
    价格向上突破收缩区间高点即入场。
    """

    @property
    def name(self) -> str:
        return "vol_squeeze_5m"

    def generate_signals(
        self,
        price: pd.Series,
        atr_period: int = 14,
        median_window: int = 50,
        squeeze_mult: float = 0.7,
        squeeze_bars: int = 24,         # 收缩持续至少 24 根（2h）
        expand_mult: float = 1.5,       # ATR 扩大出场阈值
        trend_ma: int = 200,            # 趋势过滤均线
        stop_pct: float = 2.0,          # 止损 2%
        min_gap: int = 96,              # 限频 96 根（8h）
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成波动率收缩爆发信号。"""
        n = len(price)

        # === ATR 计算（用 close 近似 TR） ===
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # ATR 的 50 期滚动中位数
        atr_median = atr.rolling(window=median_window).median()

        # === 波动率收缩检测 ===
        is_squeezed = atr < (atr_median * squeeze_mult)

        # 连续收缩计数
        squeeze_count = pd.Series(0, index=price.index, dtype=int)
        for i in range(1, n):
            if is_squeezed.iloc[i]:
                squeeze_count.iloc[i] = squeeze_count.iloc[i - 1] + 1
            else:
                squeeze_count.iloc[i] = 0

        # 收缩区间高点（过去 squeeze_bars 根内的最高价）
        squeeze_high = price.rolling(window=squeeze_bars, min_periods=1).max()

        # === 趋势过滤 ===
        ma_trend = price.rolling(window=trend_ma).mean()
        in_uptrend = price > ma_trend

        # === 入场条件 ===
        # 收缩持续达标 + 价格突破收缩区间高点 + 趋势向上
        breakout = (price > squeeze_high.shift(1)) & (price.shift(1) <= squeeze_high.shift(2))
        sufficient_squeeze = squeeze_count.shift(1) >= squeeze_bars  # 上一根仍在收缩
        raw_entries = sufficient_squeeze & breakout & in_uptrend

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
        in_trade = False

        for i in range(n):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                pnl_pct = (price.iloc[i] - entry_price) / entry_price * 100
                atr_val = atr.iloc[i] if not np.isnan(atr.iloc[i]) else 0
                med_val = atr_median.iloc[i] if not np.isnan(atr_median.iloc[i]) else 0

                # ATR 扩大到中位数 * 1.5 = 波动率爆发完成
                if med_val > 0 and atr_val > med_val * expand_mult:
                    exits.iloc[i] = True
                    in_trade = False
                # 止损
                elif pnl_pct < -stop_pct:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"VolSqueeze5m | atr={atr_period} median_w={median_window} "
            f"squeeze_bars={squeeze_bars} gap={min_gap} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def vol_squeeze_5m_signal(
    price: pd.Series,
    atr_period: int = 14,
    squeeze_bars: int = 24,
    stop_pct: float = 2.0,
    min_gap: int = 96,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return VolSqueeze5mStrategy().generate_signals(
        price, atr_period=atr_period, squeeze_bars=squeeze_bars,
        stop_pct=stop_pct, min_gap=min_gap, **kwargs,
    )
