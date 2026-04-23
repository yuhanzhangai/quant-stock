"""动量+均值回归混合策略：50/50 混合信号。

来源: Medium 文章报告此混合策略夏普 1.71，年化 56%。
动量在趋势初期表现好，均值回归在震荡期表现好，混合互补。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MomentumMeanBlendStrategy(StrategyBase):
    """动量+均值回归混合。

    动量信号：价格 > MA 且 RSI 上升 -> 做多
    均值回归信号：RSI < 30 且价格触及 BB 下轨 -> 做多
    混合：任一信号触发即入场（OR 逻辑）
    出场：RSI > 70 或跌破止损
    """

    @property
    def name(self) -> str:
        return "momentum_mean_blend"

    def generate_signals(
        self,
        price: pd.Series,
        ma_period: int = 50,
        rsi_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_entry_mom: int = 50,
        rsi_entry_mr: int = 30,
        rsi_exit: int = 70,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成混合信号。"""
        ma = price.rolling(window=ma_period).mean()

        # RSI
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # BB
        bb_mid = price.rolling(window=bb_period).mean()
        bb_std_val = price.rolling(window=bb_period).std()
        bb_lower = bb_mid - bb_std * bb_std_val

        # 动量信号：价格穿越 MA 向上 + RSI > 50
        mom_entry = (price > ma) & (price.shift(1) <= ma.shift(1)) & (rsi > rsi_entry_mom)

        # 均值回归信号：RSI < 30 + 触及 BB 下轨
        mr_entry = (rsi < rsi_entry_mr) & (price < bb_lower)
        mr_entry = mr_entry & (~mr_entry.shift(1).fillna(False))  # 边沿触发

        # 混合入场
        entries = mom_entry | mr_entry

        # 出场：RSI 超买 或 跌破 MA（趋势反转）
        rsi_overbought = (rsi > rsi_exit) & (rsi.shift(1) <= rsi_exit)
        trend_break = (price < ma) & (price.shift(1) >= ma.shift(1))
        exits = rsi_overbought | trend_break

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MomMeanBlend | ma={ma_period} rsi={rsi_period} bb={bb_period} | "
            f"入场: {entries.sum()} (mom+mr) | 出场: {exits.sum()}"
        )
        return entries, exits


def momentum_mean_blend_signal(
    price: pd.Series, ma_period: int = 50, rsi_period: int = 14,
    bb_period: int = 20, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MomentumMeanBlendStrategy().generate_signals(
        price, ma_period=ma_period, rsi_period=rsi_period, bb_period=bb_period
    )
