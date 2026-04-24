"""做空波段 trailing stop 版本：用 trailing stop 替代固定止盈。

来自 v3 研究发现：ETH/NEAR 用 trailing 2% 优于固定 TP。
应用到做空：下降趋势中跟随利润，让利润跑够再出场。

与 short_swing 的区别：
- short_swing: 固定止盈 6-8%，适合震荡市
- short_swing_trail: trailing stop，适合强趋势（让利润奔跑）

关键参数：trail_pct（从低点反弹多少止盈）
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ShortSwingTrailStrategy(StrategyBase):
    """做空波段 trailing stop 策略。"""

    @property
    def name(self) -> str:
        return "short_swing_trail"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,
        rsi_period: int = 14,
        rsi_entry: int = 55,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        min_gap: int = 288,
        stop_pct: float = 2.0,  # 止损
        trail_pct: float = 1.5,  # 从最低点反弹 1.5% 止盈
        min_profit: float = 1.0,  # 至少盈利 1% 才启动 trailing
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成做空 trailing stop 信号。"""
        n = len(price)

        # === 趋势层 ===
        ma = price.rolling(window=trend_ma).mean()
        downtrend = (price < ma) & (ma < ma.shift(20))

        # === RSI ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        rsi_drop = (rsi < rsi_entry) & (rsi.shift(1) >= rsi_entry)

        # === MACD ===
        ema_fast = price.ewm(span=macd_fast, adjust=False).mean()
        ema_slow = price.ewm(span=macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=macd_signal, adjust=False).mean()
        macd_death = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))

        # === 入场 ===
        raw_entries = downtrend & (rsi_drop | macd_death)

        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === Trailing stop 出场 ===
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

                # 做空盈亏（价格下跌=盈利）
                pnl_pct = -(price.iloc[i] - entry_price) / entry_price * 100  # 正值=盈利
                bounce = (price.iloc[i] - trough) / trough * 100 if trough > 0 else 0

                # 止损
                if (
                    price.iloc[i] > entry_price * (1 + stop_pct / 100)
                    or pnl_pct >= min_profit
                    and bounce > trail_pct
                    or price.iloc[i] > ma.iloc[i]
                ):
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortSwingTrail | ma={trend_ma} rsi={rsi_entry} gap={min_gap} "
            f"trail={trail_pct}% min_profit={min_profit}% | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def short_swing_trail_signal(
    price: pd.Series,
    min_gap: int = 288,
    trail_pct: float = 1.5,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数。"""
    return ShortSwingTrailStrategy().generate_signals(
        price,
        min_gap=min_gap,
        trail_pct=trail_pct,
        **kwargs,
    )
