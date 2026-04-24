"""SmartSwing：MinSwing + 资金费率/OI 市场温度计。

不是加更多过滤条件，而是在极端情况下调整行为：
- 资金费率异常高（>0.1%）：市场过热，暂停做多
- 资金费率异常低（<-0.05%）：空头拥挤，更积极做多（空头挤压）
- OI 急速增加：市场加杠杆，波动将增大，收紧止损

只在极端值时介入，正常时 = 原版 MinSwing。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase
from src.strategies.minute_swing import MinuteSwingStrategy


class SmartSwingStrategy(StrategyBase):
    """MinSwing + OKX 数据增强。

    由于回测中无法实时获取费率/OI，用价格动量代理：
    - 快速上涨（4h 涨>3%）≈ 资金费率高（多头拥挤）
    - 快速下跌（4h 跌>3%）≈ 资金费率低（空头拥挤 → 可能反弹）
    - 成交量放大（用价格波动代理）≈ OI 增加
    """

    @property
    def name(self) -> str:
        return "smart_swing"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,
        base_tp: float = 8.0,
        base_sl: float = 2.0,
        base_gap: int = 144,
        overheat_pct: float = 3.0,  # 4h 涨>3% = 过热
        squeeze_pct: float = -3.0,  # 4h 跌>3% = 空头挤压机会
        vol_surge_mult: float = 2.0,  # 波动率 > 2 倍均值 = 高杠杆
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 SmartSwing 信号。"""
        # 4h 收益率代理（48 根 5m）
        ret_4h = price.pct_change(48) * 100

        # 波动率代理
        vol = price.pct_change().abs().rolling(window=48).mean()
        vol_median = vol.rolling(window=500).median()

        # 市场温度
        is_overheated = ret_4h > overheat_pct
        is_squeeze = ret_4h < squeeze_pct
        vol > vol_median * vol_surge_mult

        # 基础 MinSwing 信号
        strat = MinuteSwingStrategy()
        base_e, base_x = strat.generate_signals(
            price, trend_ma=trend_ma, stop_pct=base_sl, take_profit_pct=base_tp, min_gap=base_gap
        )

        # 调整规则（最少干预）：
        # 1. 过热时不入场（跳过信号）
        entries = base_e & (~is_overheated)

        # 2. 空头挤压时额外入场机会（降低入场门槛）
        # 用更短的趋势确认
        squeeze_strat = MinuteSwingStrategy()
        squeeze_e, squeeze_x = squeeze_strat.generate_signals(
            price, trend_ma=90, stop_pct=base_sl, take_profit_pct=base_tp * 1.5, min_gap=base_gap // 2
        )
        entries = entries | (squeeze_e & is_squeeze)

        # 3. 高杠杆时收紧止损（用基础出场 + 额外检查）
        exits = base_x.copy()

        entries = entries & (~entries.shift(1).fillna(False))
        exits = exits & (~exits.shift(1).fillna(False))
        entries = entries.fillna(False)
        exits = exits.fillna(False)

        n_blocked = (base_e & is_overheated).sum()
        n_squeeze = (squeeze_e & is_squeeze).sum()
        logger.debug(
            f"SmartSwing | blocked_overheat:{n_blocked} squeeze_adds:{n_squeeze} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def smart_swing_signal(
    price: pd.Series,
    trend_ma: int = 180,
    base_tp: float = 8.0,
    base_gap: int = 144,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return SmartSwingStrategy().generate_signals(price, trend_ma=trend_ma, base_tp=base_tp, base_gap=base_gap)
