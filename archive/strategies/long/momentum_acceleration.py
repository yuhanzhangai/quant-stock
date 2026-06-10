"""动量加速度策略 (Momentum Acceleration)。

核心思路（完全不同于 MinSwing 的均线交叉体系）：
- 计算价格的二阶导数（变化率的变化率 = "加速度"）
- 当加速度从负转正 → 减速下跌 → 即将反弹 → 入场
- 当加速度从正转负 → 减速上涨 → 即将回调 → 出场
- 叠加趋势过滤（长周期均线方向）避免逆势
- min_gap 控制交易频率
- 止损 2%，止盈 8%

适用于 5m K 线。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class MomentumAccelerationStrategy(StrategyBase):
    """动量加速度策略。

    价格动量 = 价格的一阶差分的滚动平均（速度）
    动量加速度 = 速度的一阶差分的滚动平均（加速度）

    入场条件：
      1. 趋势过滤：价格 > 长期 MA 且 MA 斜率 > 0（上升趋势）
      2. 加速度零轴上穿：acc 从负转正（减速下跌，即将反弹）
      3. 动量确认：velocity > 阈值（有足够的上升动力）
    出场条件：
      1. 加速度从正转负（上涨动能衰减）
      2. 止损 2%
      3. 止盈 8%
    """

    @property
    def name(self) -> str:
        return "momentum_acceleration"

    def generate_signals(
        self,
        price: pd.Series,
        velocity_window: int = 36,  # 速度平滑窗口 (36*5m=3h)
        accel_window: int = 18,  # 加速度平滑窗口 (18*5m=1.5h)
        trend_ma: int = 240,  # 趋势过滤 MA (240*5m=20h)
        trend_slope_len: int = 48,  # 趋势斜率检测长度 (48*5m=4h)
        velocity_thresh: float = 0.0,  # 最低速度阈值（0=不过滤）
        rsi_period: int = 14,  # RSI 周期
        rsi_floor: int = 35,  # RSI 不能太超卖
        rsi_ceil: int = 65,  # RSI 不能太超买
        min_gap: int = 144,  # 最少间隔 (144*5m=12h)
        stop_pct: float = 2.0,  # 止损 2%
        take_profit_pct: float = 8.0,  # 止盈 8%
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成动量加速度信号。"""

        # === 1. 计算一阶导数：速度（动量） ===
        # 价格变化率 (pct_change) 的滚动均值
        pct_change = price.pct_change() * 100  # 百分比
        velocity = pct_change.rolling(window=velocity_window).mean()

        # === 2. 计算二阶导数：加速度 ===
        # 速度的变化率的滚动均值
        accel = velocity.diff().rolling(window=accel_window).mean()

        # === 3. 趋势过滤 ===
        ma_long = price.rolling(window=trend_ma).mean()
        ma_slope = ma_long - ma_long.shift(trend_slope_len)
        in_uptrend = (price > ma_long) & (ma_slope > 0)

        # === 4. 加速度零轴上穿 ===
        accel_cross_up = (accel > 0) & (accel.shift(1) <= 0)

        # === 5. 速度过滤 ===
        velocity_ok = velocity > velocity_thresh

        # === 6. RSI 过滤：排除极端区域 ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
        rsi_ok = (rsi > rsi_floor) & (rsi < rsi_ceil)

        # === 7. 加速度幅度过滤：只选强信号 ===
        accel_std = accel.rolling(window=velocity_window * 3).std()
        accel > 0.3 * accel_std  # 上穿后加速度要有一定幅度

        # === 原始入场信号 ===
        raw_entries = in_uptrend & accel_cross_up & velocity_ok & rsi_ok

        # === 限制频率 (min_gap) ===
        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场：加速度转负 / 止损 / 止盈 ===
        # 用更长窗口的加速度做出场，避免噪声
        accel_exit = velocity.diff().rolling(window=accel_window * 2).mean()
        accel_cross_down = (accel_exit < 0) & (accel_exit.shift(1) >= 0)

        min_hold = max(24, accel_window * 3)  # 最少持仓 2h

        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        entry_bar = 0
        in_trade = False

        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                entry_bar = i
                in_trade = True
            elif in_trade and entry_price > 0:
                pnl_pct = (price.iloc[i] - entry_price) / entry_price * 100
                bars_held = i - entry_bar

                # 止损（立即生效）
                if (
                    pnl_pct < -stop_pct
                    or pnl_pct > take_profit_pct
                    or bars_held >= min_hold
                    and pnl_pct > 0.5
                    and accel_cross_down.iloc[i]
                ):
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"MomAccel | vel_w={velocity_window} acc_w={accel_window} "
            f"trend={trend_ma} gap={min_gap} "
            f"stop={stop_pct}% tp={take_profit_pct}% | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def momentum_acceleration_signal(
    price: pd.Series,
    velocity_window: int = 36,
    accel_window: int = 18,
    trend_ma: int = 240,
    min_gap: int = 144,
    stop_pct: float = 2.0,
    take_profit_pct: float = 8.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数。"""
    return MomentumAccelerationStrategy().generate_signals(
        price,
        velocity_window=velocity_window,
        accel_window=accel_window,
        trend_ma=trend_ma,
        min_gap=min_gap,
        stop_pct=stop_pct,
        take_profit_pct=take_profit_pct,
        **kwargs,
    )
