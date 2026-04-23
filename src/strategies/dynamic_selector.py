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
from src.strategies.ichimoku_momentum import ichimoku_momentum_signal
from src.strategies.extreme_reversal import extreme_reversal_signal

# 候选策略池：只保留 4 个通过 OOS 验证的 ROBUST 策略
CANDIDATES = {
    "ExtremeRev": (extreme_reversal_signal, {"drop_period": 18, "drop_threshold": -15.0, "stabilize_bars": 3}),
    "AggrMom": (aggressive_momentum_signal, {"lookback": 50, "consec_bars": 4, "trail_atr_mult": 1.5}),
    "IchiMom_v1": (ichimoku_momentum_signal, {"tenkan": 9, "kijun": 26, "lookback": 30, "consec_bars": 3}),
    "IchiMom_v2": (ichimoku_momentum_signal, {"tenkan": 9, "kijun": 26, "lookback": 50, "consec_bars": 4}),
    "TrendMA": (trend_ma_filtered_signal, {"short_window": 25, "long_window": 200, "atr_mult": 0.5}),
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
        max_loss_pct: float = 5.0,  # 止损阈值：亏损超此比例强制切换
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """动态选择策略生成信号（带止损保护）。"""
        entries = pd.Series(False, index=price.index)
        exits = pd.Series(False, index=price.index)

        strategy_log = []
        entry_price = 0.0
        in_position = False

        i = lookback
        while i < len(price):
            # 回看窗口
            window_end = i
            window_start = max(0, i - lookback)
            window = price.iloc[window_start:window_end]

            # 检测波动率 -> 剧变时缩短评估周期
            recent_vol = window.pct_change().tail(20).std()
            hist_vol = window.pct_change().std()
            actual_step = max(step_size // 2, 21) if recent_vol > hist_vol * 2.0 else step_size

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
            forward_end = min(i + actual_step, len(price))
            forward = price.iloc[window_start:forward_end]

            if best_name != "none" and best_sharpe > 0:
                func, params = CANDIDATES[best_name]
                try:
                    e_full, x_full = func(forward, **params)
                    fwd_start = i - window_start
                    fwd_end = forward_end - window_start
                    entries.iloc[i:forward_end] = e_full.iloc[fwd_start:fwd_end].values
                    exits.iloc[i:forward_end] = x_full.iloc[fwd_start:fwd_end].values
                except Exception:
                    pass

            # 止损保护：检查持仓期间是否亏损超阈值
            for j in range(i, forward_end):
                if entries.iloc[j]:
                    entry_price = price.iloc[j]
                    in_position = True
                if in_position and entry_price > 0:
                    loss_pct = (price.iloc[j] - entry_price) / entry_price * 100
                    if loss_pct < -max_loss_pct:
                        exits.iloc[j] = True
                        in_position = False
                if exits.iloc[j]:
                    in_position = False

            strategy_log.append({
                "bar": i,
                "date": str(price.index[i]) if hasattr(price.index[i], "strftime") else i,
                "best": best_name,
                "sharpe": round(best_sharpe, 3),
                "vol_regime": "HIGH" if recent_vol > hist_vol * 1.5 else "NORMAL",
            })

            i += actual_step

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
