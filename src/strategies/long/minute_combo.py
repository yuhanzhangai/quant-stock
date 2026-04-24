"""MinSwing + IntradayMom 组合：5m 两大策略融合。

MinSwing 擅长趋势回调入场（技术面）
IntradayMom 擅长日内动量跟随（统计面）
两者互补：一个抓回调，一个抓延续。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase
from src.strategies.intraday_momentum import IntradayMomentumStrategy
from src.strategies.minute_swing import MinuteSwingStrategy


class MinuteComboStrategy(StrategyBase):
    """5m 双策略组合。

    OR 入场：任一策略触发即入场
    AND 出场：两个策略都出场才平仓（保守出场，让利润跑）
    """

    @property
    def name(self) -> str:
        return "minute_combo"

    def generate_signals(
        self,
        price: pd.Series,
        # MinSwing params
        ms_trend_ma: int = 180,
        ms_tp: float = 6.0,
        ms_sl: float = 2.0,
        ms_gap: int = 36,
        # IntradayMom params
        im_session: int = 96,
        im_threshold: float = 0.008,
        im_hold: int = 192,
        im_sl: float = 1.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成组合信号。"""
        e1, x1 = MinuteSwingStrategy().generate_signals(
            price, trend_ma=ms_trend_ma, take_profit_pct=ms_tp, stop_pct=ms_sl, min_gap=ms_gap
        )
        e2, x2 = IntradayMomentumStrategy().generate_signals(
            price, session_bars=im_session, momentum_threshold=im_threshold, hold_bars=im_hold, stop_pct=im_sl
        )

        # OR 入场
        entries = e1 | e2
        entries = entries & (~entries.shift(1).fillna(False))

        # 出场：任一出场即平（更安全）
        exits = x1 | x2
        exits = exits & (~exits.shift(1).fillna(False))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(f"MinCombo | e1:{e1.sum()} e2:{e2.sum()} -> combined:{entries.sum()} | 出场: {exits.sum()}")
        return entries, exits


def minute_combo_signal(
    price: pd.Series,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MinuteComboStrategy().generate_signals(price, **kwargs)
