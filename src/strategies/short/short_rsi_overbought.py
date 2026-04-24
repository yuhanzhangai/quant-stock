"""RSI 超买做空策略：RSI > 70 回落时入场做空。

设计理念：
- 在下降趋势中，RSI 达到超买区域说明反弹过度
- 当 RSI 从超买区回落时，价格大概率继续下跌
- 结合布林带上轨触碰确认超买

信号逻辑：
  入场 = 下降趋势 + RSI 从超买区回落（跌破 70/65）+ 价格触碰布林带上轨
  出场 = 固定止盈/止损 + RSI 极度超卖反弹 + 趋势反转

特点：比 short_swing 的 RSI=60 入场更激进，等更确定的超买信号
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ShortRSIOverboughtStrategy(StrategyBase):
    """RSI 超买做空策略。

    多重超买确认：
    1. RSI > overbought 后回落（超买信号）
    2. 价格触碰或超过布林带上轨（价格偏离）
    3. 下降趋势中（主趋势支持做空）
    """

    @property
    def name(self) -> str:
        return "short_rsi_overbought"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,
        rsi_period: int = 14,
        rsi_overbought: int = 70,  # RSI 超买阈值
        rsi_entry_cross: int = 65,  # RSI 跌破此值入场（从超买回落确认）
        bb_period: int = 20,  # 布林带周期
        bb_std: float = 2.0,  # 布林带标准差倍数
        min_gap: int = 144,
        stop_pct: float = 2.0,
        take_profit_pct: float = 5.0,  # 比 short_swing 的 8% 更小，更频繁止盈
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 RSI 超买做空信号。"""
        n = len(price)

        # === 趋势层 ===
        ma = price.rolling(window=trend_ma).mean()
        ma_slope = ma - ma.shift(20)
        downtrend = (price < ma) & (ma_slope < 0)

        # === RSI ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # RSI 从超买区回落：之前有过 RSI > overbought，现在跌破 entry_cross
        was_overbought = rsi.rolling(window=12).max() > rsi_overbought  # 最近 12 根内有过超买
        rsi_falling = (rsi < rsi_entry_cross) & (rsi.shift(1) >= rsi_entry_cross)
        rsi_signal = was_overbought & rsi_falling

        # === 布林带 ===
        bb_ma = price.rolling(window=bb_period).mean()
        bb_std_val = price.rolling(window=bb_period).std()
        bb_upper = bb_ma + bb_std * bb_std_val

        # 最近有触碰过上轨（价格过度偏离）
        touched_upper = price.rolling(window=12).max() >= bb_upper.shift(1)

        # === 入场 ===
        raw_entries = downtrend & rsi_signal & touched_upper

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
                price_change = (price.iloc[i] - entry_price) / entry_price * 100

                # 止损
                if (
                    price_change > stop_pct
                    or price_change < -take_profit_pct
                    or rsi.iloc[i] < 20
                    and i > 0
                    and rsi.iloc[i] > rsi.iloc[i - 1]
                    or price.iloc[i] > ma.iloc[i]
                ):
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortRSIOverbought | rsi_ob={rsi_overbought} entry_cross={rsi_entry_cross} "
            f"bb={bb_period}/{bb_std} gap={min_gap} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def short_rsi_overbought_signal(
    price: pd.Series,
    trend_ma: int = 180,
    rsi_overbought: int = 70,
    min_gap: int = 144,
    stop_pct: float = 2.0,
    take_profit_pct: float = 5.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数。"""
    return ShortRSIOverboughtStrategy().generate_signals(
        price,
        trend_ma=trend_ma,
        rsi_overbought=rsi_overbought,
        min_gap=min_gap,
        stop_pct=stop_pct,
        take_profit_pct=take_profit_pct,
        **kwargs,
    )
