"""ROBUST Ensemble：只用通过 OOS 验证的 4 个策略组合。

4 个 ROBUST 策略（全部 OOS 验证通过）：
1. ExtremeReversal  - 大跌后抄底（事件驱动）
2. AggressiveMom    - 趋势追涨（动量）
3. IchimokuMomentum - 趋势确认入场（技术面）
4. TrendMA_Filtered - 保守趋势跟踪（均线）

互补性：
- 趋势市：AggrMom + IchiMom + TrendMA 活跃
- 急跌后：ExtremeReversal 活跃
- 策略之间低相关，组合更稳
"""

import pandas as pd
from loguru import logger

from src.strategies.aggressive_momentum import AggressiveMomentumStrategy
from src.strategies.base import StrategyBase
from src.strategies.extreme_reversal import ExtremeReversalStrategy
from src.strategies.ichimoku_momentum import IchimokuMomentumStrategy
from src.strategies.trend_ma_filtered import TrendMAFilteredStrategy


class RobustEnsemble(StrategyBase):
    """ROBUST 四策略 Ensemble。

    入场：任意 1 个策略触发即入场（OR 逻辑，因为 4 个策略互补覆盖不同场景）
    出场：多数策略出场（2/4 出场即平仓）
    """

    @property
    def name(self) -> str:
        return "robust_ensemble"

    def generate_signals(
        self,
        price: pd.Series,
        min_entry: int = 1,
        min_exit: int = 2,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 ROBUST ensemble 信号。"""

        # 4 个 ROBUST 策略
        e1, x1 = ExtremeReversalStrategy().generate_signals(price, drop_threshold=-15.0, stabilize_bars=3)
        e2, x2 = AggressiveMomentumStrategy().generate_signals(price, lookback=50, consec_bars=4, trail_atr_mult=1.5)
        e3, x3 = IchimokuMomentumStrategy().generate_signals(price, tenkan=9, kijun=26, lookback=50, consec_bars=4)
        e4, x4 = TrendMAFilteredStrategy().generate_signals(price, short_window=25, long_window=200, atr_mult=0.5)

        # 窗口入场投票（5 根内有信号算 1 票）
        vote_window = 3
        v1 = e1.astype(int).rolling(window=vote_window, min_periods=1).max()
        v2 = e2.astype(int).rolling(window=vote_window, min_periods=1).max()
        v3 = e3.astype(int).rolling(window=vote_window, min_periods=1).max()
        v4 = e4.astype(int).rolling(window=vote_window, min_periods=1).max()

        entry_votes = v1 + v2 + v3 + v4
        exit_votes = x1.astype(int) + x2.astype(int) + x3.astype(int) + x4.astype(int)

        entries = entry_votes >= min_entry
        exits = exit_votes >= min_exit

        entries = entries & (~entries.shift(1).fillna(False))
        exits = exits & (~exits.shift(1).fillna(False))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"RobustEnsemble | min_entry={min_entry} min_exit={min_exit} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def robust_ensemble_signal(
    price: pd.Series,
    min_entry: int = 1,
    min_exit: int = 2,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return RobustEnsemble().generate_signals(price, min_entry=min_entry, min_exit=min_exit)
