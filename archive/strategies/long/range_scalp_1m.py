"""1m 横盘做 T 策略：检测区间 → 下轨买入 → 中轨/上轨卖出 → 循环。

核心逻辑：
1. 区间检测：BB 宽度收窄 + ADX < 20 = 横盘确认
2. 入场：价格触及 BB 下轨 + RSI < 35
3. 出场：价格回到 BB 中轨（保守）或上轨（激进）
4. 保护：价格突破区间（跌破下轨 2 个 ATR）= 止损出局
5. 频率：横盘时每 30-60 分钟可做一次 T

盈利来源：横盘中价格在上下轨之间反复震荡，每次赚 0.3-0.8%
关键：只在横盘时开启，趋势市立刻停止
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


def detect_ranging(
    price: pd.Series, bb_period: int = 20, adx_period: int = 14, bb_squeeze_mult: float = 0.8
) -> pd.Series:
    """检测横盘区间。

    横盘条件：
    1. BB 宽度 < 历史中位数 * squeeze_mult
    2. 价格变化率绝对值 < 0.5%/小时
    """
    bb_mid = price.rolling(window=bb_period).mean()
    bb_std = price.rolling(window=bb_period).std()
    bb_width = (2 * bb_std) / bb_mid  # 归一化宽度

    bb_width_median = bb_width.rolling(window=200).median()
    is_narrow = bb_width < bb_width_median * bb_squeeze_mult

    # 价格变化率低（60 根 1m = 1 小时）
    hourly_change = price.pct_change(60).abs()
    is_slow = hourly_change < 0.005  # 1 小时变化 < 0.5%

    return is_narrow & is_slow


class RangeScalp1mStrategy(StrategyBase):
    """1m 横盘做 T。

    只在横盘区间内交易：
    - 下轨买入 → 中轨卖出（保守，盈利 ~0.3-0.5%）
    - 下轨买入 → 上轨卖出（激进，盈利 ~0.6-1.0%）
    - 突破区间 → 止损
    """

    @property
    def name(self) -> str:
        return "range_scalp_1m"

    def generate_signals(
        self,
        price: pd.Series,
        bb_period: int = 20,
        bb_std_mult: float = 2.0,
        rsi_period: int = 14,
        rsi_entry: int = 35,
        rsi_exit: int = 55,
        exit_at_mid: bool = True,
        min_gap: int = 30,  # 最少 30 根间隔（30 分钟）
        stop_atr_mult: float = 1.5,  # 止损 = 下轨 - 1.5*ATR
        atr_period: int = 14,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成横盘做 T 信号。"""
        # BB
        bb_mid = price.rolling(window=bb_period).mean()
        bb_std = price.rolling(window=bb_period).std()
        bb_upper = bb_mid + bb_std_mult * bb_std
        bb_lower = bb_mid - bb_std_mult * bb_std

        # RSI
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # ATR（止损用）
        high = price.rolling(2).max()
        low = price.rolling(2).min()
        prev_close = price.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # 区间检测
        is_ranging = detect_ranging(price, bb_period=bb_period)

        # 入场：横盘 + 价格触及下轨 + RSI 超卖
        touch_lower = price <= bb_lower
        rsi_oversold = rsi < rsi_entry
        raw_entries = is_ranging & touch_lower & rsi_oversold

        # 限频
        entries = pd.Series(False, index=price.index)
        last = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last) >= min_gap:
                entries.iloc[i] = True
                last = i

        # 出场
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False

        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                # 止盈：回到中轨或上轨
                if exit_at_mid:
                    if price.iloc[i] >= bb_mid.iloc[i]:
                        exits.iloc[i] = True
                        in_trade = False
                else:
                    if price.iloc[i] >= bb_upper.iloc[i]:
                        exits.iloc[i] = True
                        in_trade = False

                # RSI 回到中性也出场
                if rsi.iloc[i] > rsi_exit:
                    exits.iloc[i] = True
                    in_trade = False

                # 止损：跌破下轨 - ATR（区间被打破）
                stop_price = bb_lower.iloc[i] - atr.iloc[i] * stop_atr_mult
                if price.iloc[i] < stop_price:
                    exits.iloc[i] = True
                    in_trade = False

                # 区间消失也出场（趋势开始了）
                if not is_ranging.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        n_ranging = is_ranging.sum()
        logger.debug(
            f"RangeScalp1m | ranging_bars:{n_ranging}/{len(price)} "
            f"({n_ranging / len(price) * 100:.0f}%) | "
            f"entries:{entries.sum()} exits:{exits.sum()}"
        )
        return entries, exits


class RangeScalpComboStrategy(StrategyBase):
    """组合策略：横盘做 T + 趋势做 MinSwing。

    检测市场状态：
    - 横盘 → RangeScalp（做 T 赚震荡钱）
    - 趋势 → MinSwing（趋势跟踪赚趋势钱）
    - 两个策略互不干扰，覆盖所有市场状态
    """

    @property
    def name(self) -> str:
        return "range_scalp_combo"

    def generate_signals(
        self,
        price: pd.Series,
        # 横盘做 T 参数
        bb_period: int = 20,
        rsi_entry: int = 35,
        min_gap_range: int = 30,
        # 趋势 MinSwing 参数（1m 版本）
        trend_ma: int = 900,
        min_gap_trend: int = 720,
        tp_trend: float = 5.0,
        sl_trend: float = 1.5,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """横盘 + 趋势组合信号。"""
        is_ranging = detect_ranging(price, bb_period=bb_period)

        # 横盘信号
        range_strat = RangeScalp1mStrategy()
        e_range, x_range = range_strat.generate_signals(
            price, bb_period=bb_period, rsi_entry=rsi_entry, min_gap=min_gap_range
        )

        # 趋势信号（简化版 MinSwing for 1m）
        ma = price.rolling(window=trend_ma).mean()
        uptrend = (price > ma) & (ma > ma.shift(100))

        ema12 = price.ewm(span=12, adjust=False).mean()
        ema26 = price.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        sig = macd.ewm(span=9, adjust=False).mean()
        macd_cross = (macd > sig) & (macd.shift(1) <= sig.shift(1))

        raw_trend = uptrend & macd_cross & (~is_ranging)
        e_trend = pd.Series(False, index=price.index)
        last = -min_gap_trend * 2
        for i in range(len(raw_trend)):
            if raw_trend.iloc[i] and (i - last) >= min_gap_trend:
                e_trend.iloc[i] = True
                last = i

        x_trend = pd.Series(False, index=price.index)
        ep = 0.0
        in_t = False
        for i in range(len(price)):
            if e_trend.iloc[i]:
                ep = price.iloc[i]
                in_t = True
            elif in_t and ep > 0:
                pnl = (price.iloc[i] - ep) / ep * 100
                if pnl < -sl_trend or pnl > tp_trend or price.iloc[i] < ma.iloc[i]:
                    x_trend.iloc[i] = True
                    in_t = False

        # 合并
        entries = e_range | e_trend
        exits = x_range | x_trend

        entries = entries & (~entries.shift(1).fillna(False))
        exits = exits & (~exits.shift(1).fillna(False))

        n_range_entries = e_range.sum()
        n_trend_entries = e_trend.sum()
        logger.debug(
            f"RangeCombo | range_entries:{n_range_entries} trend_entries:{n_trend_entries} | total:{entries.sum()}"
        )
        return entries.fillna(False), exits.fillna(False)


def range_scalp_1m_signal(price, **kwargs):
    return RangeScalp1mStrategy().generate_signals(price, **kwargs)


def range_scalp_combo_signal(price, **kwargs):
    return RangeScalpComboStrategy().generate_signals(price, **kwargs)
