"""多时间框架做空策略：4h确认趋势 + 5m找入场。

设计理念：
- 大时间框架(4h)确认大趋势方向 → 只在4h级别熊市才做空
- 小时间框架(5m)找精确入场点 → 用已验证的 trend_follow 信号
- 叠加 session 过滤 → 排除美盘

这是"三层过滤"策略:
  Layer 1: 4h MA 方向（大趋势）
  Layer 2: 5m 双均线死叉+MACD（入场信号）
  Layer 3: 时段过滤（可选）

需要同时传入 4h 和 5m 数据。
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase


class ShortMultiTFStrategy(StrategyBase):
    """多时间框架做空策略。"""

    @property
    def name(self) -> str:
        return "short_multi_tf"

    def generate_signals(
        self,
        price: pd.Series,
        price_4h: pd.Series | None = None,  # 4h 收盘价（可选）
        # 4h 层参数
        htf_ma: int = 50,                    # 4h MA 周期（50*4h=200h≈8天）
        htf_slope_bars: int = 5,             # 4h MA 斜率计算周期
        # 5m 层参数（沿用 trend_follow 最优）
        fast_ma: int = 84,
        slow_ma: int = 180,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        # 时段过滤
        use_session: bool = True,
        session_start: int = 20,
        session_end: int = 13,
        # 交易参数
        min_gap: int = 288,
        stop_pct: float = 3.0,
        take_profit_pct: float = 10.0,
        trail_pct: float = 1.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成多时间框架做空信号。"""
        n = len(price)

        # === Layer 1: 4h 大趋势确认 ===
        if price_4h is not None and len(price_4h) >= htf_ma + 10:
            htf_ma_series = price_4h.rolling(window=htf_ma).mean()
            htf_slope = htf_ma_series - htf_ma_series.shift(htf_slope_bars)
            htf_bearish = (price_4h < htf_ma_series) & (htf_slope < 0)

            # 将 4h 信号对齐到 5m 时间索引（forward fill）
            htf_bearish_5m = htf_bearish.reindex(price.index, method="ffill").fillna(False)
        else:
            # 没有 4h 数据时用 5m 的长 MA 替代（720 = 180*4 ≈ 4h 的 MA180）
            long_ma = price.rolling(window=720).mean()
            long_slope = long_ma - long_ma.shift(60)
            htf_bearish_5m = (price < long_ma) & (long_slope < 0)

        # === Layer 2: 5m 入场信号（trend_follow 逻辑）===
        ma_fast = price.rolling(window=fast_ma).mean()
        ma_slow = price.rolling(window=slow_ma).mean()

        bearish_cross = ma_fast < ma_slow
        ma_fast_slope = ma_fast - ma_fast.shift(10)
        ma_slow_slope = ma_slow - ma_slow.shift(10)
        both_declining = (ma_fast_slope < 0) & (ma_slow_slope < 0)
        price_below = (price < ma_fast) & (price < ma_slow)

        ema_f = price.ewm(span=macd_fast, adjust=False).mean()
        ema_s = price.ewm(span=macd_slow, adjust=False).mean()
        macd_line = ema_f - ema_s
        signal_line = macd_line.ewm(span=macd_signal, adjust=False).mean()
        macd_death = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))

        ltf_signal = bearish_cross & both_declining & price_below & macd_death

        # === Layer 3: 时段过滤 ===
        if use_session and hasattr(price.index, 'hour'):
            if session_start > session_end:
                in_session = (price.index.hour >= session_start) | (price.index.hour < session_end)
            else:
                in_session = (price.index.hour >= session_start) & (price.index.hour < session_end)
        else:
            in_session = pd.Series(True, index=price.index)

        # === 三层叠加入场 ===
        raw_entries = htf_bearish_5m & ltf_signal & in_session

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

                pnl = (price.iloc[i] - entry_price) / entry_price * 100
                bounce = (price.iloc[i] - trough) / trough * 100 if trough > 0 else 0

                if pnl > stop_pct:
                    exits.iloc[i] = True
                    in_trade = False
                elif pnl < -take_profit_pct:
                    exits.iloc[i] = True
                    in_trade = False
                elif bounce > trail_pct and pnl < -2.0:
                    exits.iloc[i] = True
                    in_trade = False
                elif ma_fast.iloc[i] > ma_slow.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortMultiTF | htf_ma={htf_ma} fast={fast_ma} slow={slow_ma} "
            f"session={'UTC'+str(session_start)+'-'+str(session_end) if use_session else 'OFF'} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits
