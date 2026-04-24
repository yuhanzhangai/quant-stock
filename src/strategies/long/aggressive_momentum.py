"""激进动量策略：追涨杀跌 + 波动率放大 + 杠杆仓位管理。"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class AggressiveMomentumStrategy(StrategyBase):
    """激进动量策略。

    核心逻辑：
    1. 价格创 N 日新高 + 成交量放大 -> 追涨入场
    2. 连续 K 根 K 线上涨 -> 加速确认
    3. 止损：跌破入场价 X% -> 立即平仓
    4. 止盈：价格离开 ATR 通道 -> 移动止盈
    """

    @property
    def name(self) -> str:
        return "aggressive_momentum"

    def generate_signals(
        self,
        price: pd.Series,
        lookback: int = 20,
        consec_bars: int = 3,
        stop_loss_pct: float = 3.0,
        trail_atr_mult: float = 2.0,
        atr_period: int = 14,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成激进动量信号。

        Args:
            lookback: 创新高回看周期
            consec_bars: 连续上涨确认根数
            stop_loss_pct: 止损百分比
            trail_atr_mult: 移动止盈 ATR 倍数
            atr_period: ATR 周期
        """
        # 创 N 日新高
        rolling_high = price.rolling(window=lookback).max()
        new_high = price >= rolling_high

        # 连续上涨确认
        up_bars = (price > price.shift(1)).rolling(window=consec_bars).sum()
        momentum_confirm = up_bars >= consec_bars

        # 短期动量加速（5 根 K 线收益 > 长期平均的 2 倍）
        ret_5 = price.pct_change(5)
        ret_avg = ret_5.rolling(window=50).mean()
        ret_std = ret_5.rolling(window=50).std()
        accel = ret_5 > ret_avg + ret_std

        # 入场：新高 + 连续上涨 + 动量加速
        entries = new_high & momentum_confirm & accel

        # ATR 移动止盈
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # 出场：价格跌破最近高点 - ATR * mult
        recent_high = price.rolling(window=lookback).max()
        trail_stop = recent_high - atr * trail_atr_mult
        exits = price < trail_stop

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"AggressiveMom | lookback={lookback} consec={consec_bars} "
            f"stop={stop_loss_pct}% trail={trail_atr_mult}x | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


class MultiFactorAggressive(StrategyBase):
    """多因子激进策略。

    组合多个信号投票：
    - 价格 > MA200（趋势确认）
    - RSI 在 40-65 之间（不超买不超卖，蓄力区）
    - 短期动量 > 0（近期在涨）
    - 波动率正在扩大（ATR 上升）
    需要至少 3/4 个因子同意才入场。
    """

    @property
    def name(self) -> str:
        return "multi_factor_aggressive"

    def generate_signals(
        self,
        price: pd.Series,
        ma_period: int = 200,
        rsi_period: int = 14,
        mom_period: int = 10,
        atr_period: int = 14,
        min_votes: int = 3,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成多因子信号。"""
        # 因子 1: 趋势
        ma = price.rolling(window=ma_period).mean()
        f_trend = price > ma

        # 因子 2: RSI 蓄力区
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        f_rsi = (rsi > 40) & (rsi < 65)

        # 因子 3: 短期动量
        mom = price.pct_change(mom_period)
        f_mom = mom > 0

        # 因子 4: 波动率扩大
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()
        atr_ma = atr.rolling(window=50).mean()
        f_vol = atr > atr_ma

        # 投票
        votes = f_trend.astype(int) + f_rsi.astype(int) + f_mom.astype(int) + f_vol.astype(int)

        # 入场：新达到投票阈值
        above_threshold = votes >= min_votes
        entries = above_threshold & (~above_threshold.shift(1).fillna(False))

        # 出场：跌破 MA 或 RSI 超买
        exits = (price < ma) | (rsi > 80)
        exits = exits & (~exits.shift(1).fillna(False))  # 只取边沿

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MultiFactor | ma={ma_period} rsi={rsi_period} mom={mom_period} "
            f"votes>={min_votes} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def aggressive_momentum_signal(
    price: pd.Series,
    lookback: int = 20,
    consec_bars: int = 3,
    trail_atr_mult: float = 2.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return AggressiveMomentumStrategy().generate_signals(
        price, lookback=lookback, consec_bars=consec_bars, trail_atr_mult=trail_atr_mult
    )


def multi_factor_signal(
    price: pd.Series,
    ma_period: int = 200,
    mom_period: int = 10,
    min_votes: int = 3,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return MultiFactorAggressive().generate_signals(
        price, ma_period=ma_period, mom_period=mom_period, min_votes=min_votes
    )
