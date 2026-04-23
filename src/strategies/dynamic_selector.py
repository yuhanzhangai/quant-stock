"""动态策略选择器：根据最近 N 天各策略的模拟表现，自动选用当期最优策略。

核心思路：
- 维护一个滑动窗口（如最近 30 天 / 180 根 4h K 线）
- 在窗口内回测每个候选策略
- 选夏普最高的策略，用于下一个窗口的交易
- 每隔 step_size 根 K 线重新评估
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase
from src.strategies.trend_ma_filtered import trend_ma_filtered_signal
from src.strategies.aggressive_momentum import aggressive_momentum_signal
from src.strategies.rsi_extreme import rsi_extreme_signal
from src.strategies.momentum_breakout import momentum_breakout_signal
from src.strategies.mean_reversion_bb import mean_reversion_bb_signal
from src.strategies.ichimoku import ichimoku_signal

# 候选策略池
CANDIDATES = {
    "TrendMA": (trend_ma_filtered_signal, {"short_window": 25, "long_window": 200, "atr_mult": 0.5}),
    "AggrMom": (aggressive_momentum_signal, {"lookback": 50, "consec_bars": 4, "trail_atr_mult": 1.5}),
    "RSI": (rsi_extreme_signal, {"rsi_period": 14, "oversold": 25, "overbought": 75, "trend_ma": 200}),
    "Breakout": (momentum_breakout_signal, {"entry_window": 50, "exit_window": 20}),
    "MeanRev": (mean_reversion_bb_signal, {"bb_period": 20, "bb_std": 2.0}),
    "Ichimoku": (ichimoku_signal, {"tenkan": 9, "kijun": 26, "senkou_b": 52}),
}


def _quick_sharpe(price: pd.Series, entries: pd.Series, exits: pd.Series) -> float:
    """快速计算夏普（不用 vectorbt，避免开销）。"""
    pos = pd.Series(0, index=price.index)
    in_trade = False
    for i in range(len(price)):
        if entries.iloc[i] and not in_trade:
            in_trade = True
        elif exits.iloc[i] and in_trade:
            in_trade = False
        pos.iloc[i] = 1 if in_trade else 0

    strat_returns = price.pct_change() * pos.shift(1)
    strat_returns = strat_returns.dropna()
    if len(strat_returns) < 10 or strat_returns.std() == 0:
        return 0.0
    return float(strat_returns.mean() / strat_returns.std() * np.sqrt(6 * 365))


class DynamicSelectorStrategy(StrategyBase):
    """动态策略选择器。"""

    @property
    def name(self) -> str:
        return "dynamic_selector"

    def generate_signals(
        self,
        price: pd.Series,
        lookback: int = 180,  # 回看窗口（4h K线数 = 30天）
        step_size: int = 42,  # 重评估间隔（42 * 4h = 7天）
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """动态选择策略生成信号。"""
        entries = pd.Series(False, index=price.index)
        exits = pd.Series(False, index=price.index)

        strategy_log = []

        i = lookback
        while i < len(price):
            # 回看窗口
            window_end = i
            window_start = max(0, i - lookback)
            window = price.iloc[window_start:window_end]

            # 评估每个候选策略
            best_name = "none"
            best_sharpe = -999.0

            for name, (func, params) in CANDIDATES.items():
                try:
                    e, x = func(window, **params)
                    sharpe = _quick_sharpe(window, e, x)
                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_name = name
                except Exception:
                    continue

            # 用最优策略生成下一个 step 的信号
            forward_end = min(i + step_size, len(price))
            forward = price.iloc[window_start:forward_end]  # 需要足够回看

            if best_name != "none" and best_sharpe > 0:
                func, params = CANDIDATES[best_name]
                try:
                    e_full, x_full = func(forward, **params)
                    # 只取 forward 部分的信号
                    entries.iloc[i:forward_end] = e_full.iloc[i - window_start:forward_end - window_start].values
                    exits.iloc[i:forward_end] = x_full.iloc[i - window_start:forward_end - window_start].values
                except Exception:
                    pass

            strategy_log.append({
                "bar": i,
                "date": str(price.index[i]) if hasattr(price.index[i], "strftime") else i,
                "best": best_name,
                "sharpe": round(best_sharpe, 3),
            })

            i += step_size

        # 日志
        if strategy_log:
            selections = pd.DataFrame(strategy_log)
            counts = selections["best"].value_counts()
            logger.debug(f"DynamicSelector | 策略选择分布: {dict(counts)}")

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(f"DynamicSelector | 入场: {entries.sum()} | 出场: {exits.sum()}")
        return entries, exits


def dynamic_selector_signal(
    price: pd.Series, lookback: int = 180, step_size: int = 42,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return DynamicSelectorStrategy().generate_signals(
        price, lookback=lookback, step_size=step_size
    )
