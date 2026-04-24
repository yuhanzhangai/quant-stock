"""
Strategy Status: Candidate
Strategy Name: fast_exit_eth
Strategy Version: 1.0.0
Research State: Needs more OOS validation
Allowed Changes:
- bug fix
- logging
Not Allowed:
- silent parameter changes
- unrecorded optimization

FastExit 组合策略：MinSwing 入场 + 快 MA 死叉早出场。

仅 ETH 有效（+34% 改进），其他币用原版 MinSwing。
"""

import pandas as pd

from src.strategies.base import StrategyBase
from src.strategies.minute_swing import MinuteSwingStrategy


class FastExitStrategy(StrategyBase):
    @property
    def name(self) -> str:
        return "fast_exit"

    def generate_signals(self, price, fast_ma=90, profit_thr=0.3, trend_ma=180, sl=2.0, tp=8.0, gap=144, **kw):
        strat = MinuteSwingStrategy()
        entries, _ = strat.generate_signals(price, trend_ma=trend_ma, stop_pct=sl, take_profit_pct=tp, min_gap=gap)
        sma_f = price.rolling(window=fast_ma).mean()
        sma_h = price.rolling(window=fast_ma // 2).mean()
        death = (sma_h < sma_f) & (sma_h.shift(1) >= sma_f.shift(1))
        ma_slow = price.rolling(window=trend_ma).mean()

        exits = pd.Series(False, index=price.index)
        ep = 0.0
        in_t = False
        for i in range(len(price)):
            if entries.iloc[i]:
                ep = price.iloc[i]
                in_t = True
            elif in_t and ep > 0:
                pnl = (price.iloc[i] - ep) / ep * 100
                if pnl < -sl or pnl > tp or price.iloc[i] < ma_slow.iloc[i] or death.iloc[i] and pnl > profit_thr:
                    exits.iloc[i] = True
                    in_t = False
        return entries, exits.fillna(False)


def fast_exit_signal(price, **kw):
    return FastExitStrategy().generate_signals(price, **kw)
