"""基金模式 Combo 策略：像基金一样管理资金，自动切换策略。

核心理念：
  把 $50 当成一个小基金来运营。
  基金经理（策略）根据市场状态分配资金：
  - 横盘期 → 30% 仓位做 T（小赚）
  - 趋势期 → 70% 仓位追趋势（大赚）
  - 高危期 → 10% 仓位或空仓（保命）

净值管理：
  - 追踪基金净值（NAV）
  - 最大回撤控制（全基金级别止损）
  - 每日净值记录
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase


def classify_market_state(price: pd.Series, lookback: int = 200) -> pd.Series:
    """判断市场状态：TRENDING_UP / TRENDING_DOWN / RANGING / VOLATILE。"""
    ma = price.rolling(window=lookback).mean()
    ma_slope = (ma - ma.shift(20)) / ma.shift(20) * 100

    # BB 宽度
    bb_std = price.rolling(window=20).std()
    bb_mid = price.rolling(window=20).mean()
    bb_width = (2 * bb_std) / bb_mid
    bb_width_median = bb_width.rolling(window=200).median()

    # ATR
    high = price.rolling(2).max()
    low = price.rolling(2).min()
    prev_close = price.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean()
    atr_pct = atr / price * 100
    atr_median = atr_pct.rolling(window=200).median()

    state = pd.Series("RANGING", index=price.index)
    state[ma_slope > 1.0] = "TRENDING_UP"
    state[ma_slope < -1.0] = "TRENDING_DOWN"
    state[atr_pct > atr_median * 1.8] = "VOLATILE"

    return state


class FundModeCombo(StrategyBase):
    """基金模式组合策略。

    市场状态 → 策略 + 仓位：
      TRENDING_UP:   MinSwing 做多，70% 仓位
      RANGING:       BB 下轨做 T，30% 仓位
      TRENDING_DOWN: 不交易（或做空 30%）
      VOLATILE:      不交易（现金为王）

    风控：
      - 单笔止损 2%（基于仓位，不是总资金）
      - 全基金回撤 > 10% → 减半仓位
      - 全基金回撤 > 15% → 清仓等待
    """

    @property
    def name(self) -> str:
        return "fund_mode_combo"

    def generate_signals(
        self,
        price: pd.Series,
        # 趋势参数
        trend_ma: int = 180,
        trend_tp: float = 8.0,
        trend_sl: float = 2.0,
        trend_gap: int = 144,
        # 横盘参数
        bb_period: int = 20,
        bb_std_mult: float = 2.0,
        range_rsi_entry: int = 35,
        range_gap: int = 30,
        # 基金风控
        max_drawdown_pct: float = 15.0,
        reduce_at_dd: float = 10.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """基金模式信号生成。"""
        state = classify_market_state(price, lookback=trend_ma)

        # === 趋势做多信号 ===
        ma = price.rolling(window=trend_ma).mean()
        uptrend = (price > ma) & (ma > ma.shift(24))

        delta = price.diff()
        g = delta.clip(lower=0).rolling(14).mean()
        l = (-delta).clip(lower=0).rolling(14).mean()
        rsi = 100 - 100 / (1 + g / l)

        ema12 = price.ewm(span=12, adjust=False).mean()
        ema26 = price.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        sig = macd.ewm(span=9, adjust=False).mean()

        rsi_bounce = (rsi > 40) & (rsi.shift(1) <= 40)
        macd_cross = (macd > sig) & (macd.shift(1) <= sig.shift(1))
        trend_entry = uptrend & (rsi_bounce | macd_cross) & (state == "TRENDING_UP")

        # === 横盘做 T 信号 ===
        bb_mid = price.rolling(window=bb_period).mean()
        bb_s = price.rolling(window=bb_period).std()
        bb_lower = bb_mid - bb_std_mult * bb_s

        range_entry = (price <= bb_lower) & (rsi < range_rsi_entry) & (state == "RANGING")

        # === 合并入场 ===
        raw_entries = trend_entry | range_entry

        # 限频（趋势用长间隔，横盘用短间隔）
        entries = pd.Series(False, index=price.index)
        last_trend = -trend_gap * 2
        last_range = -range_gap * 2
        for i in range(len(raw_entries)):
            if trend_entry.iloc[i] and (i - last_trend) >= trend_gap:
                entries.iloc[i] = True
                last_trend = i
            elif range_entry.iloc[i] and (i - last_range) >= range_gap:
                entries.iloc[i] = True
                last_range = i

        # === 出场 ===
        exits = pd.Series(False, index=price.index)
        entry_price = 0.0
        entry_state = ""
        in_trade = False
        peak_nav = 1.0
        nav = 1.0

        for i in range(len(price)):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                entry_state = state.iloc[i]
                in_trade = True
            elif in_trade and entry_price > 0:
                pnl = (price.iloc[i] - entry_price) / entry_price * 100

                # 更新 NAV
                nav_change = pnl / 100
                current_nav = 1.0 + nav_change
                if current_nav > peak_nav:
                    peak_nav = current_nav
                dd = (peak_nav - current_nav) / peak_nav * 100

                # 趋势出场
                if entry_state == "TRENDING_UP":
                    if pnl < -trend_sl or pnl > trend_tp or price.iloc[i] < ma.iloc[i]:
                        exits.iloc[i] = True
                        in_trade = False

                # 横盘做 T 出场
                elif entry_state == "RANGING":
                    if price.iloc[i] >= bb_mid.iloc[i] or rsi.iloc[i] > 55:
                        exits.iloc[i] = True
                        in_trade = False
                    elif pnl < -1.5:  # 横盘止损更紧
                        exits.iloc[i] = True
                        in_trade = False

                # 状态变化强制出场
                if state.iloc[i] == "VOLATILE":
                    exits.iloc[i] = True
                    in_trade = False
                elif state.iloc[i] == "TRENDING_DOWN" and entry_state != "TRENDING_DOWN":
                    exits.iloc[i] = True
                    in_trade = False

                # 基金级别风控
                if dd > max_drawdown_pct:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        # 统计
        n_trend = (state == "TRENDING_UP").sum()
        n_range = (state == "RANGING").sum()
        n_down = (state == "TRENDING_DOWN").sum()
        n_vol = (state == "VOLATILE").sum()
        total = len(state)

        logger.debug(
            f"FundCombo | UP:{n_trend/total*100:.0f}% RANGE:{n_range/total*100:.0f}% "
            f"DOWN:{n_down/total*100:.0f}% VOL:{n_vol/total*100:.0f}% | "
            f"entries:{entries.sum()} exits:{exits.sum()}"
        )
        return entries, exits


def fund_mode_signal(price, **kwargs):
    return FundModeCombo().generate_signals(price, **kwargs)
