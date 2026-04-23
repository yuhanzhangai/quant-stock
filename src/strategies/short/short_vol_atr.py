"""成交量+ATR波动率做空策略：用全新因子维度做空。

设计理念（与锚点策略完全不同的因子）：
- 不用 MA 死叉、不用 RSI、不用 MACD
- 纯粹基于「成交量异常 + 波动率放大」的恐慌检测
- 放量下跌 = 恐慌抛售开始 → 做空
- ATR 放大 = 波动率扩张 → 趋势加速期

信号逻辑：
  入场 = 成交量异常放大(>2倍均量) + ATR扩张 + 价格下行(收阴) + 价格在MA下方
  出场 = ATR收缩(波动率回落) + trailing stop + 固定止损

核心创新：用量价关系替代技术指标，捕捉不同维度的alpha
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase


class ShortVolATRStrategy(StrategyBase):
    """成交量+ATR波动率做空策略。"""

    @property
    def name(self) -> str:
        return "short_vol_atr"

    def generate_signals(
        self,
        price: pd.Series,
        volume: pd.Series | None = None,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
        # 成交量参数
        vol_ma_period: int = 60,           # 成交量均线周期（60*5m=5h）
        vol_spike_mult: float = 2.0,       # 放量倍数阈值
        # ATR 参数
        atr_period: int = 14,
        atr_expand_mult: float = 1.5,      # ATR > 均值*1.5 = 波动率扩张
        # 趋势过滤（轻量，只做基本方向确认）
        trend_ma: int = 120,
        # 入场确认
        bearish_bars: int = 3,             # 最近N根中收阴占多数
        min_gap: int = 192,
        # 出场
        stop_pct: float = 2.5,
        trail_pct: float = 1.5,
        atr_contract_exit: bool = True,    # ATR收缩时出场
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成成交量+ATR做空信号。

        注意：需要传入 volume, high, low 数据。
        如果不传，则只用 price 的近似值。
        """
        n = len(price)

        # === 成交量分析 ===
        if volume is not None and len(volume) == n:
            vol = volume
        else:
            # 没有成交量数据时用价格波动幅度近似
            vol = abs(price - price.shift(1))

        vol_ma = vol.rolling(window=vol_ma_period).mean()
        vol_spike = vol > (vol_ma * vol_spike_mult)

        # 最近 6 根内有过放量
        recent_vol_spike = vol_spike.rolling(window=6).max().fillna(0).astype(bool)

        # === ATR 波动率 ===
        if high is not None and low is not None and len(high) == n:
            tr = pd.concat([
                high - low,
                abs(high - price.shift(1)),
                abs(low - price.shift(1)),
            ], axis=1).max(axis=1)
        else:
            # 用价格变动近似 True Range
            tr = abs(price - price.shift(1))

        atr = tr.rolling(window=atr_period).mean()
        atr_ma = atr.rolling(window=atr_period * 3).mean()  # ATR 的长期均值
        atr_expanding = atr > (atr_ma * atr_expand_mult)

        # === 趋势方向（轻量过滤）===
        ma = price.rolling(window=trend_ma).mean()
        below_ma = price < ma

        # === 价格下行确认 ===
        bearish = price < price.shift(1)
        bearish_count = bearish.rolling(window=bearish_bars).sum()
        mostly_bearish = bearish_count >= bearish_bars - 1

        # === 入场：放量 + ATR扩张 + 价格下行 + 在MA下方 ===
        raw_entries = recent_vol_spike & atr_expanding & mostly_bearish & below_ma

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
        trough = 0.0
        entry_atr = 0.0
        in_trade = False

        for i in range(n):
            if entries.iloc[i]:
                entry_price = price.iloc[i]
                trough = entry_price
                entry_atr = atr.iloc[i] if not pd.isna(atr.iloc[i]) else 0
                in_trade = True
            elif in_trade and entry_price > 0:
                if price.iloc[i] < trough:
                    trough = price.iloc[i]

                pnl = (price.iloc[i] - entry_price) / entry_price * 100
                bounce = (price.iloc[i] - trough) / trough * 100 if trough > 0 else 0

                # 止损
                if pnl > stop_pct:
                    exits.iloc[i] = True
                    in_trade = False
                # Trailing stop
                elif bounce > trail_pct and pnl < -0.5:
                    exits.iloc[i] = True
                    in_trade = False
                # ATR 收缩出场（波动率回归正常 = 恐慌结束）
                elif atr_contract_exit and entry_atr > 0:
                    current_atr = atr.iloc[i] if not pd.isna(atr.iloc[i]) else 0
                    if current_atr < entry_atr * 0.6 and pnl < 0:  # ATR缩小40%
                        exits.iloc[i] = True
                        in_trade = False
                # 价格回到 MA 上方
                elif price.iloc[i] > ma.iloc[i]:
                    exits.iloc[i] = True
                    in_trade = False

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"ShortVolATR | vol_mult={vol_spike_mult} atr_expand={atr_expand_mult} "
            f"gap={min_gap} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits
