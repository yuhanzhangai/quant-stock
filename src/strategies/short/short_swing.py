"""做空波段策略：MinuteSwing 的镜像做空版。

设计目标：
- 5m K 线级别
- 做空专用：捕捉下跌趋势中的反弹后回落
- 用"反转价格"技巧让 vectorbt (只支持做多) 模拟做空盈亏
- 入场：RSI 从超买回落 + MACD 死叉
- 止盈：价格下跌 8%（做空盈利）
- 止损：价格上涨 2%（做空亏损）
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


def invert_price(price: pd.Series) -> pd.Series:
    """反转价格序列，使做多反转价格 = 做空原始价格。

    公式: price_inv = price[0] * 2 - price
    当原始价格下跌 delta，反转价格上涨 delta，从而用做多模拟做空。
    """
    return price.iloc[0] * 2 - price


class ShortSwingStrategy(StrategyBase):
    """做空波段策略。

    趋势判断：价格 < MA(180) 且 MA 向下 → 下降趋势
    入场条件：RSI 从超买回落（RSI 跌破 60）+ MACD 死叉
    出场条件：止盈 8%（价格下跌）/ 止损 2%（价格上涨）/ 趋势反转
    """

    @property
    def name(self) -> str:
        return "short_swing"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,           # 大趋势 MA 周期（180*5m=15h）
        rsi_period: int = 14,
        rsi_entry: int = 60,           # RSI 跌破此值触发
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        min_gap: int = 144,            # 最少间隔（144*5m=12h）
        stop_pct: float = 2.0,         # 止损：价格上涨 2%（做空亏损）
        take_profit_pct: float = 8.0,  # 止盈：价格下跌 8%（做空盈利）
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """在原始价格上生成做空信号。

        注意：返回的 entries/exits 是基于 **原始价格** 的做空信号。
        回测时需对价格做 invert_price() 再配合这些信号使用。
        """
        # === 趋势层：下降趋势 ===
        ma_trend = price.rolling(window=trend_ma).mean()
        # 价格低于 MA 且 MA 向下
        downtrend = (price < ma_trend) & (ma_trend < ma_trend.shift(20))

        # === RSI ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # RSI 从超买回落：RSI 跌破 rsi_entry
        rsi_drop = (rsi < rsi_entry) & (rsi.shift(1) >= rsi_entry)

        # === MACD 死叉 ===
        ema_fast = price.ewm(span=macd_fast, adjust=False).mean()
        ema_slow = price.ewm(span=macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=macd_signal, adjust=False).mean()
        macd_death_cross = (
            (macd_line < signal_line) &
            (macd_line.shift(1) >= signal_line.shift(1))
        )

        # === 入场：下降趋势 + RSI 回落 + MACD 死叉 ===
        raw_entries = downtrend & (rsi_drop | macd_death_cross)

        # 限制频率
        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场：做空止损/止盈 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False

        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                # 做空: 价格上涨=亏损, 价格下跌=盈利
                price_change_pct = (price.iloc[i] - entry_price) / entry_price * 100
                # 止损：价格上涨超过 stop_pct
                if price_change_pct > stop_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # 止盈：价格下跌超过 take_profit_pct
                elif price_change_pct < -take_profit_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # 趋势反转（价格回到 MA 上方）
                elif price.iloc[i] > ma_trend.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortSwing | trend_ma={trend_ma} rsi_entry={rsi_entry} "
            f"stop={stop_pct}% tp={take_profit_pct}% | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def short_swing_signal(
    price: pd.Series,
    trend_ma: int = 180,
    stop_pct: float = 2.0,
    take_profit_pct: float = 8.0,
    min_gap: int = 144,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数：生成做空波段信号。"""
    return ShortSwingStrategy().generate_signals(
        price,
        trend_ma=trend_ma,
        stop_pct=stop_pct,
        take_profit_pct=take_profit_pct,
        min_gap=min_gap,
        **kwargs,
    )
