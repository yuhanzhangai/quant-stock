"""布林带均值回归策略：价格触及下轨开多，触及上轨平仓。"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MeanReversionBBStrategy(StrategyBase):
    """布林带均值回归策略。

    - 价格跌破下轨 -> 开多（超卖反弹）
    - 价格突破上轨或回到中轨 -> 平仓
    - 可选 RSI 确认：RSI < 30 时才开仓
    """

    @property
    def name(self) -> str:
        return "mean_reversion_bb"

    def generate_signals(
        self,
        price: pd.Series,
        bb_period: int = 20,
        bb_std: float = 2.0,
        exit_at_mid: bool = True,
        rsi_filter: bool = True,
        rsi_period: int = 14,
        rsi_threshold: int = 35,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成布林带反转信号。

        Args:
            price: 收盘价
            bb_period: 布林带周期
            bb_std: 标准差倍数
            exit_at_mid: True=回到中轨平仓, False=触及上轨平仓
            rsi_filter: 是否用 RSI 过滤
            rsi_period: RSI 周期
            rsi_threshold: RSI 阈值（低于此值才开仓）
        """
        mid = price.rolling(window=bb_period).mean()
        std = price.rolling(window=bb_period).std()
        upper = mid + bb_std * std
        lower = mid - bb_std * std

        # 入场：价格从上方穿越下轨
        touch_lower = (price < lower) & (price.shift(1) >= lower.shift(1))

        # 趋势过滤：只在横盘/非强趋势区间做均值回归
        # 用布林带宽度判断：宽度收窄 = 横盘（适合均值回归）
        bb_width = (upper - lower) / mid
        bb_width_ma = bb_width.rolling(window=50).mean()
        is_range_bound = bb_width < bb_width_ma * 1.5  # 带宽不超过均值1.5倍

        if rsi_filter:
            delta = price.diff()
            gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
            losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
            rs = gains / losses
            rsi = 100 - (100 / (1 + rs))
            entries = touch_lower & (rsi < rsi_threshold) & is_range_bound
        else:
            entries = touch_lower & is_range_bound

        # 出场
        if exit_at_mid:
            exits = (price > mid) & (price.shift(1) <= mid.shift(1))
        else:
            touch_upper = (price > upper) & (price.shift(1) <= upper.shift(1))
            exits = touch_upper

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MeanRevBB | period={bb_period} std={bb_std} exit_mid={exit_at_mid} "
            f"rsi={rsi_filter} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def mean_reversion_bb_signal(
    price: pd.Series,
    bb_period: int = 20,
    bb_std: float = 2.0,
    exit_at_mid: bool = True,
    rsi_filter: bool = True,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """独立函数版本。"""
    strategy = MeanReversionBBStrategy()
    return strategy.generate_signals(
        price,
        bb_period=bb_period,
        bb_std=bb_std,
        exit_at_mid=exit_at_mid,
        rsi_filter=rsi_filter,
    )
