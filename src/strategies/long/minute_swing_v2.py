"""MinuteSwing v2：加入 regime 感知 + 动态止盈。

v1 问题：seg1 大亏（错误时期交易）
v2 改进：
1. 波动率 regime：高波动时不交易（避免被频繁止损）
2. 动态止盈：根据 ATR 调整止盈幅度
3. 趋势确认更严格：需要多级别确认
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MinuteSwingV2Strategy(StrategyBase):
    """MinuteSwing v2：regime-aware。"""

    @property
    def name(self) -> str:
        return "minute_swing_v2"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,
        fast_ma: int = 12,
        rsi_period: int = 14,
        rsi_entry: int = 40,
        atr_period: int = 14,
        min_gap: int = 36,
        base_tp: float = 4.0,
        base_sl: float = 2.0,
        vol_filter: bool = True,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 v2 信号。"""
        # === Regime 检测 ===
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat([
            high - low, (high - prev_close).abs(), (low - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()
        atr_pct = atr / price * 100
        atr_median = atr_pct.rolling(window=500).median()

        # 高波动 = 不交易（被频繁止损的根源）
        if vol_filter:
            safe_vol = atr_pct < atr_median * 1.3
        else:
            safe_vol = pd.Series(True, index=price.index)

        # === 趋势 ===
        ma_long = price.rolling(window=trend_ma).mean()
        ma_fast = price.rolling(window=fast_ma).mean()
        ma_mid = price.rolling(window=60).mean()

        # 三级确认：价格 > 长MA，快MA > 中MA，长MA 向上
        uptrend = (price > ma_long) & (ma_fast > ma_mid) & (ma_long > ma_long.shift(24))

        # === RSI ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        rsi_bounce = (rsi > rsi_entry) & (rsi.shift(1) <= rsi_entry)

        # === MACD 确认 ===
        ema12 = price.ewm(span=12, adjust=False).mean()
        ema26 = price.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        macd_positive = macd > macd_signal

        # === 入场 ===
        raw_entries = uptrend & safe_vol & (rsi_bounce | macd_positive) & (rsi < 60)

        # 限频
        entries = pd.Series(False, index=price.index)
        last = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last) >= min_gap:
                entries.iloc[i] = True
                last = i

        # === 动态止盈止损 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False
        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                pnl = (price.iloc[i] - entry_price) / entry_price * 100
                # 动态止盈：低波动时目标高，高波动时目标低
                current_atr = atr_pct.iloc[i] if not pd.isna(atr_pct.iloc[i]) else 1.0
                median_atr = atr_median.iloc[i] if not pd.isna(atr_median.iloc[i]) else 1.0
                vol_ratio = current_atr / max(median_atr, 0.1)
                dynamic_tp = base_tp / max(vol_ratio, 0.5)  # 高波动时缩小目标
                dynamic_sl = base_sl * max(vol_ratio, 0.5)  # 高波动时放大止损

                if pnl < -dynamic_sl or pnl > dynamic_tp:
                    exits.iloc[i] = True
                    in_trade = False
                # 趋势反转强制出场
                elif price.iloc[i] < ma_long.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MinSwingV2 | safe_bars:{safe_vol.sum()}/{len(price)} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def minute_swing_v2_signal(
    price: pd.Series, trend_ma: int = 180, min_gap: int = 36,
    base_tp: float = 4.0, base_sl: float = 2.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MinuteSwingV2Strategy().generate_signals(
        price, trend_ma=trend_ma, min_gap=min_gap, base_tp=base_tp, base_sl=base_sl
    )
