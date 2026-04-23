"""熊市反弹做空策略：做空死猫反弹。

设计理念：
- 在确认的下降趋势中，价格反弹至均线附近是做空的最佳时机
- "Dead Cat Bounce"：急跌后的技术性反弹往往是卖出/做空良机
- 等反弹到阻力位再入场，风险收益比远优于追跌

信号逻辑：
  入场 = 下降趋势 + 价格反弹至 MA 附近但被拒绝 + RSI 回升至中性区后回落
  出场 = 价格创新低止盈 + 固定止损 + 反弹成功止损

关键创新：不是追跌入场，而是等反弹高点入场，天然止损点小
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class ShortBounceFadeStrategy(StrategyBase):
    """熊市反弹做空策略。

    核心思路：
    1. 确认大趋势向下（价格 < MA，MA 斜率为负）
    2. 等待价格反弹接近 MA（到达阻力区）
    3. 当反弹被拒绝（价格开始回落、RSI 从高位回落）时入场做空
    4. 止损设在 MA 上方（如果突破 MA 说明反弹成功，认输）
    5. 止盈在新低或固定百分比
    """

    @property
    def name(self) -> str:
        return "short_bounce_fade"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,               # 趋势 MA 周期
        ma_proximity_pct: float = 1.0,     # 价格距 MA 多近算"接近"（%）
        rsi_period: int = 14,
        rsi_bounce_high: int = 55,         # RSI 反弹到此处后回落 = 入场
        rsi_bounce_low: int = 25,          # RSI 跌到此处 = 反弹起点
        lookback_decline: int = 48,        # 回看 N 根确认之前有过下跌（48*5m=4h）
        decline_pct: float = 3.0,          # 之前下跌至少 3% 才算有效
        min_gap: int = 192,                # 最少间隔（比 short_swing 宽松，等好机会）
        stop_pct: float = 2.0,             # 止损：价格继续上涨 2%
        take_profit_pct: float = 6.0,      # 止盈：价格下跌 6%
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """在原始价格上生成做空反弹信号。"""
        n = len(price)

        # === 趋势层：下降趋势 ===
        ma = price.rolling(window=trend_ma).mean()
        ma_slope = ma - ma.shift(20)
        downtrend = (price < ma) & (ma_slope < 0)

        # === 价格接近 MA（反弹到阻力位）===
        distance_to_ma = (ma - price) / ma * 100  # 正值=价格在MA下方
        near_ma = distance_to_ma < ma_proximity_pct  # 价格已经反弹到很接近 MA

        # === RSI 反弹后回落 ===
        delta = price.diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta).clip(lower=0).rolling(window=rsi_period).mean()
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

        # RSI 从上方跌破 bounce_high（反弹结束的信号）
        rsi_reject = (rsi < rsi_bounce_high) & (rsi.shift(1) >= rsi_bounce_high)

        # === 确认之前有过显著下跌（真正的死猫反弹前提）===
        price_min_lookback = price.rolling(window=lookback_decline).min()
        had_prior_decline = (price - price_min_lookback) / price_min_lookback * 100 > decline_pct

        # === 价格开始回落（今天收阴）===
        price_falling = price < price.shift(1)

        # === 入场 ===
        raw_entries = downtrend & near_ma & rsi_reject & had_prior_decline & price_falling

        # 限制频率
        entries = pd.Series(False, index=price.index)
        last_entry_idx = -min_gap * 2
        for i in range(len(raw_entries)):
            if raw_entries.iloc[i] and (i - last_entry_idx) >= min_gap:
                entries.iloc[i] = True
                last_entry_idx = i

        # === 出场 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        in_trade = False

        for i in range(n):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                price_change = (price.iloc[i] - entry_price) / entry_price * 100

                # 止损：价格继续上涨（反弹成功）
                if price_change > stop_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # 止盈：价格大幅下跌
                elif price_change < -take_profit_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # 突破 MA 止损（趋势可能反转）
                elif price.iloc[i] > ma.iloc[i] * 1.005:  # 突破 MA 0.5% 以上
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortBounceFade | trend_ma={trend_ma} prox={ma_proximity_pct}% "
            f"rsi_high={rsi_bounce_high} gap={min_gap} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def short_bounce_fade_signal(
    price: pd.Series,
    trend_ma: int = 180,
    min_gap: int = 192,
    stop_pct: float = 2.0,
    take_profit_pct: float = 6.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数。"""
    return ShortBounceFadeStrategy().generate_signals(
        price,
        trend_ma=trend_ma,
        min_gap=min_gap,
        stop_pct=stop_pct,
        take_profit_pct=take_profit_pct,
        **kwargs,
    )
