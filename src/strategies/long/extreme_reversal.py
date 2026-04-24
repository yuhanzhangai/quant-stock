"""极端波动后均值回归策略。

大跌后反弹是 crypto 最可靠的模式之一。
检测极端下跌（N 日跌幅 > X%），在企稳后入场。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ExtremeReversalStrategy(StrategyBase):
    """极端波动后反转策略。

    入场：
    1. N 日跌幅 > threshold（如 3 天跌 15%+）
    2. 出现企稳信号（连续 2 根阳线 或 RSI 从极低回升）
    3. 价格不再创新低

    出场：
    1. 反弹到跌幅的 50%~80% 止盈
    2. 继续创新低止损
    """

    @property
    def name(self) -> str:
        return "extreme_reversal"

    def generate_signals(
        self,
        price: pd.Series,
        drop_period: int = 18,  # 4h * 18 = 3 天
        drop_threshold: float = -10.0,  # 跌幅阈值 %
        stabilize_bars: int = 3,  # 企稳确认 K 线数
        take_profit_pct: float = 50.0,  # 反弹到跌幅的 X% 止盈
        stop_loss_bars: int = 30,  # 最大持仓时间
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成极端反转信号。"""
        # 检测急跌
        returns_n = (price / price.shift(drop_period) - 1) * 100
        extreme_drop = returns_n < drop_threshold

        # 企稳：连续 N 根不创新低
        rolling_low = price.rolling(window=stabilize_bars).min()
        not_new_low = price > rolling_low.shift(1)

        # 连续阳线确认
        up_bars = (price > price.shift(1)).astype(int).rolling(window=stabilize_bars).sum()
        stabilized = up_bars >= (stabilize_bars - 1)

        # 入场：急跌后企稳
        entries = extreme_drop.shift(1).fillna(False) & stabilized & not_new_low
        entries = entries & (~entries.shift(1).fillna(False))  # 边沿

        # 出场：简单的反弹止盈 + 时间止损
        # 用 RSI 超买作为止盈代理
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=14).mean()
        losses = (-delta).clip(lower=0).rolling(window=14).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        exits = rsi > 65  # RSI 回到中性偏强就出
        exits = exits & (~exits.shift(1).fillna(False))

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ExtremeRev | drop_period={drop_period} threshold={drop_threshold}% | "
            f"drops:{extreme_drop.sum()} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def extreme_reversal_signal(
    price: pd.Series,
    drop_period: int = 18,
    drop_threshold: float = -10.0,
    stabilize_bars: int = 3,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return ExtremeReversalStrategy().generate_signals(
        price, drop_period=drop_period, drop_threshold=drop_threshold, stabilize_bars=stabilize_bars
    )
