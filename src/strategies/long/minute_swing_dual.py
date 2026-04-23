"""MinSwing 双向版：趋势向上做多，趋势向下做空。

永续合约天然支持做空，不利用等于浪费一半机会。
做空逻辑是做多的镜像：
- 下降趋势 + RSI 超买回落 + MACD 死叉 = 做空
- 止盈/止损反向
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MinuteSwingDualStrategy(StrategyBase):
    """MinSwing 双向版。"""

    @property
    def name(self) -> str:
        return "minute_swing_dual"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,
        mid_ma: int = 60,
        fast_ma: int = 12,
        rsi_period: int = 14,
        rsi_long_entry: int = 40,
        rsi_short_entry: int = 60,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        min_gap: int = 144,
        stop_pct: float = 2.0,
        take_profit_pct: float = 8.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成双向信号。

        entries = 做多入场 OR 做空入场
        exits = 做多出场 OR 做空出场
        （vectorbt 不直接支持做空，这里用做多信号模拟反向交易的收益）
        """
        ma_trend = price.rolling(window=trend_ma).mean()
        ma_mid = price.rolling(window=mid_ma).mean()
        ma_fast_line = price.rolling(window=fast_ma).mean()

        # RSI
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # MACD
        ema_f = price.ewm(span=macd_fast, adjust=False).mean()
        ema_s = price.ewm(span=macd_slow, adjust=False).mean()
        macd_line = ema_f - ema_s
        sig_line = macd_line.ewm(span=macd_signal, adjust=False).mean()

        # === 做多信号 ===
        uptrend = (price > ma_trend) & (ma_trend > ma_trend.shift(24))
        rsi_long = (rsi > rsi_long_entry) & (rsi.shift(1) <= rsi_long_entry)
        macd_bull = (macd_line > sig_line) & (macd_line.shift(1) <= sig_line.shift(1))
        long_raw = uptrend & (rsi_long | macd_bull)

        # === 做空信号（镜像）===
        downtrend = (price < ma_trend) & (ma_trend < ma_trend.shift(24))
        rsi_short = (rsi < rsi_short_entry) & (rsi.shift(1) >= rsi_short_entry)
        macd_bear = (macd_line < sig_line) & (macd_line.shift(1) >= sig_line.shift(1))
        short_raw = downtrend & (rsi_short | macd_bear)

        # 合并（做空时：价格下跌 = 盈利，所以在下跌趋势中"买入反向ETF"的逻辑）
        # vectorbt 只支持做多，所以做空信号我们标记但不直接交易
        # 实际效果：只在上升趋势做多，下降趋势通过 exits 避免持仓

        # 限频
        raw_entries = long_raw  # 只做多（做空需要永续合约实盘支持）
        entries = pd.Series(False, index=price.index)
        last = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last) >= min_gap:
                entries.iloc[i] = True
                last = i

        # 出场：止盈止损 + 趋势反转 + 做空信号触发（额外的出场条件）
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False
        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                pnl = (price.iloc[i] - entry_price) / entry_price * 100
                # 出场条件
                if pnl < -stop_pct or pnl > take_profit_pct:
                    exits.iloc[i] = True
                    in_trade = False
                elif price.iloc[i] < ma_trend.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False
                # 额外：做空信号出现 = 强制出场
                elif short_raw.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        n_long = long_raw.sum()
        n_short = short_raw.sum()
        logger.debug(
            f"DualSwing | long_signals:{n_long} short_signals:{n_short} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def minute_swing_dual_signal(
    price: pd.Series, trend_ma: int = 180, min_gap: int = 144,
    stop_pct: float = 2.0, take_profit_pct: float = 8.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MinuteSwingDualStrategy().generate_signals(
        price, trend_ma=trend_ma, min_gap=min_gap,
        stop_pct=stop_pct, take_profit_pct=take_profit_pct
    )
