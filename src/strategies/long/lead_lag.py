"""跨币种领先-滞后策略：BTC 先动，ALT 跟。

学术研究证实 BTC 价格变动领先 ETH/ALT 约 5-30 分钟。
当 BTC 快速上涨时，做多 ETH（等待跟涨）。

这不是传统的技术分析，而是市场微结构效应。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class LeadLagStrategy(StrategyBase):
    """BTC 领先 -> ETH/ALT 跟随策略。

    使用同一币种的短/长期动量差作为代理：
    - 短期（5-15分钟）急涨 = "BTC 刚涨"
    - 但中期还没涨满 = "还有空间跟"
    """

    @property
    def name(self) -> str:
        return "lead_lag"

    def generate_signals(
        self,
        price: pd.Series,
        fast_bars: int = 3,  # 最近 15 分钟（3*5m）的涨幅
        slow_bars: int = 24,  # 最近 2 小时的涨幅
        fast_threshold: float = 0.005,  # 短期涨 > 0.5%
        slow_max: float = 0.02,  # 中期涨幅 < 2%（还有空间）
        hold_bars: int = 12,  # 持仓 1 小时
        stop_pct: float = 0.5,  # 紧止损 0.5%
        min_gap: int = 24,  # 2 小时间隔
        trend_ma: int = 200,  # 趋势过滤
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成领先-滞后信号。"""
        # 短期急涨
        fast_ret = price.pct_change(fast_bars)
        # 中期涨幅
        slow_ret = price.pct_change(slow_bars)

        # 趋势过滤
        ma = price.rolling(window=trend_ma).mean()
        uptrend = price > ma

        # 入场：短期急涨 + 中期还没涨满 + 趋势向上
        raw_entries = (fast_ret > fast_threshold) & (slow_ret < slow_max) & (slow_ret > 0) & uptrend

        # 限频
        entries = pd.Series(False, index=price.index)
        last = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last) >= min_gap:
                entries.iloc[i] = True
                last = i

        # 出场：固定持仓时间 或 止损
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False
        bars_held = 0
        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
                bars_held = 0
            elif in_trade:
                bars_held += 1
                pnl = (price.iloc[i] - entry_price) / entry_price * 100
                if pnl < -stop_pct or bars_held >= hold_bars:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"LeadLag | fast={fast_bars} slow={slow_bars} hold={hold_bars} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def lead_lag_signal(
    price: pd.Series,
    fast_bars: int = 3,
    hold_bars: int = 12,
    stop_pct: float = 0.5,
    min_gap: int = 24,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return LeadLagStrategy().generate_signals(
        price, fast_bars=fast_bars, hold_bars=hold_bars, stop_pct=stop_pct, min_gap=min_gap
    )
