"""分钟线 Donchian 突破策略：通道突破 + ATR 移动止损 + 趋势过滤。

设计目标：
- 5m K 线级别
- Donchian 通道突破入场（120 根 5m = 10h 窗口）
- ATR 移动止损（最高价 - 2*ATR）
- MA(300) 趋势过滤（约 25h）
- 止盈 3%
- min_gap 限频（72 根 = 6h）
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MinuteBreakoutStrategy(StrategyBase):
    """分钟线 Donchian 突破策略。

    入场：价格突破最近 N 根最高价 + 价格 > MA(300)
    出场：ATR 移动止损 或 止盈 3%
    限频：两次入场至少间隔 min_gap 根 K 线
    """

    @property
    def name(self) -> str:
        return "minute_breakout"

    def generate_signals(
        self,
        price: pd.Series,
        donchian_period: int = 120,     # 突破窗口（120*5m=10h）
        atr_period: int = 20,           # ATR 周期
        atr_stop_mult: float = 2.0,     # ATR 止损倍数
        trend_ma: int = 300,            # 趋势 MA（300*5m=25h）
        min_gap: int = 72,              # 最少间隔（72*5m=6h）
        take_profit_pct: float = 3.0,   # 止盈 3%
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 Donchian 突破分钟线信号。"""
        # === 趋势过滤：价格 > MA(300) 才做多 ===
        ma_trend = price.rolling(window=trend_ma).mean()
        trend_ok = price > ma_trend

        # === Donchian 通道：最近 N 根最高价 ===
        donchian_high = price.rolling(window=donchian_period).max()
        # 突破 = 当前价 > 前一根的 Donchian 高点
        breakout = price > donchian_high.shift(1)

        # === ATR 计算 ===
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # === 原始入场信号：突破 + 趋势确认 ===
        raw_entries = breakout & trend_ok

        # === 限频：至少间隔 min_gap 根 K 线 ===
        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场：ATR 移动止损 + 止盈 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        trade_high = 0.0
        in_trade = False

        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                trade_high = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                cur_price = price.iloc[i]
                # 更新持仓期间最高价
                if cur_price > trade_high:
                    trade_high = cur_price

                # ATR 移动止损：最高价 - 2*ATR
                atr_val = atr.iloc[i] if pd.notna(atr.iloc[i]) else 0.0
                stop_level = trade_high - atr_stop_mult * atr_val

                # 止盈：涨幅 > take_profit_pct%
                pnl_pct = (cur_price - entry_price) / entry_price * 100

                if cur_price < stop_level:
                    exits.iloc[i] = True
                    in_trade = False
                elif pnl_pct > take_profit_pct:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MinBreakout | donchian={donchian_period} atr_stop={atr_stop_mult} "
            f"trend_ma={trend_ma} tp={take_profit_pct}% gap={min_gap} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def minute_breakout_signal(
    price: pd.Series,
    donchian_period: int = 120,
    atr_period: int = 20,
    atr_stop_mult: float = 2.0,
    trend_ma: int = 300,
    min_gap: int = 72,
    take_profit_pct: float = 3.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数：生成分钟线 Donchian 突破信号。"""
    return MinuteBreakoutStrategy().generate_signals(
        price,
        donchian_period=donchian_period,
        atr_period=atr_period,
        atr_stop_mult=atr_stop_mult,
        trend_ma=trend_ma,
        min_gap=min_gap,
        take_profit_pct=take_profit_pct,
    )
