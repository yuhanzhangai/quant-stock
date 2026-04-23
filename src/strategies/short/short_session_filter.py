"""时段过滤做空策略：利用亚洲时段信号质量更高的发现。

来自 v3 研究：亚洲时段(UTC 0-8)信号质量最高(68%胜率)。
本策略在 trend_follow 基础上加入时段过滤，只在特定时段入场。

信号逻辑：
  入场 = trend_follow 的所有条件 + UTC 时段过滤
  出场 = 与 trend_follow 相同

核心假设：不同时段的市场参与者不同，亚洲时段趋势信号更可靠
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ShortSessionFilterStrategy(StrategyBase):
    """时段过滤做空策略。"""

    @property
    def name(self) -> str:
        return "short_session"

    def generate_signals(
        self,
        price: pd.Series,
        # 均线参数（沿用 trend_follow 最优）
        fast_ma: int = 84,
        slow_ma: int = 180,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        # 时段过滤
        session_start: int = 0,            # UTC 开始小时
        session_end: int = 12,             # UTC 结束小时（亚洲+欧洲早盘）
        # 交易参数
        min_gap: int = 288,
        stop_pct: float = 3.0,
        take_profit_pct: float = 10.0,
        trail_pct: float = 1.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成时段过滤做空信号。"""
        n = len(price)

        # === 均线系统（与 trend_follow 相同）===
        ma_fast = price.rolling(window=fast_ma).mean()
        ma_slow = price.rolling(window=slow_ma).mean()

        bearish_cross = ma_fast < ma_slow
        ma_fast_slope = ma_fast - ma_fast.shift(10)
        ma_slow_slope = ma_slow - ma_slow.shift(10)
        both_declining = (ma_fast_slope < 0) & (ma_slow_slope < 0)
        price_below_both = (price < ma_fast) & (price < ma_slow)

        # === MACD ===
        ema_fast = price.ewm(span=macd_fast, adjust=False).mean()
        ema_slow = price.ewm(span=macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=macd_signal, adjust=False).mean()
        macd_death = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))

        # === 时段过滤 ===
        if hasattr(price.index, 'hour'):
            if session_start < session_end:
                in_session = (price.index.hour >= session_start) & (price.index.hour < session_end)
            else:  # 跨午夜
                in_session = (price.index.hour >= session_start) | (price.index.hour < session_end)
        else:
            in_session = pd.Series(True, index=price.index)

        # === 入场 ===
        raw_entries = bearish_cross & both_declining & price_below_both & macd_death & in_session

        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场（与 trend_follow 相同）===
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
            f"ShortSession | session=UTC{session_start}-{session_end} "
            f"fast={fast_ma} slow={slow_ma} gap={min_gap} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits
