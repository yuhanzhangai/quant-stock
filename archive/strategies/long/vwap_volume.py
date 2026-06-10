"""VWAP + 成交量放大 分钟线策略。

模拟 VWAP（成交量加权移动均值），结合放量突破入场。
价格上穿 VWAP 且成交量放大 = 入场；价格跌破 VWAP = 出场。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class VWAPVolumeStrategy(StrategyBase):
    """VWAP 量价策略。

    入场条件：
    1. 价格从下方穿越 VWAP（上穿）
    2. 当前成交量 > 20 期均量 * 1.5（放量确认）
    3. 距离上次入场 >= min_gap 根 K 线（限频）

    出场条件：
    价格跌破 VWAP
    """

    @property
    def name(self) -> str:
        return "vwap_volume"

    def generate_signals(
        self,
        price: pd.Series,
        vwap_period: int = 20,
        vol_ma_period: int = 20,
        vol_mult: float = 1.5,
        min_gap: int = 6,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 VWAP + 放量信号。

        Args:
            price: 收盘价序列
            vwap_period: VWAP 滚动窗口
            vol_ma_period: 成交量均线窗口
            vol_mult: 放量倍数阈值
            min_gap: 两次入场最小间隔（K 线根数）
        """
        # --- volume proxy: 价格变化绝对值 ---
        vol_proxy = price.diff().abs()

        # --- 模拟 VWAP: 成交量加权价格均值 ---
        # VWAP ≈ sum(price * vol) / sum(vol)  滚动窗口
        pv = price * vol_proxy
        vwap = pv.rolling(window=vwap_period).sum() / vol_proxy.rolling(window=vwap_period).sum()

        # --- 成交量条件 ---
        vol_ma = vol_proxy.rolling(window=vol_ma_period).mean()
        vol_surge = vol_proxy > vol_ma * vol_mult

        # --- 价格上穿 VWAP ---
        cross_up = (price > vwap) & (price.shift(1) <= vwap.shift(1))

        # --- 原始入场 ---
        raw_entries = cross_up & vol_surge

        # --- min_gap 限频 ---
        entries = raw_entries.copy().astype(bool)
        last_entry_idx = -min_gap - 1  # 确保第一个信号不被过滤
        for i in range(len(entries)):
            if entries.iloc[i]:
                if (i - last_entry_idx) < min_gap:
                    entries.iloc[i] = False
                else:
                    last_entry_idx = i

        # --- 出场：价格跌破 VWAP ---
        cross_down = (price < vwap) & (price.shift(1) >= vwap.shift(1))
        exits = cross_down.fillna(False)

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"VWAP_Vol | vwap_period={vwap_period} vol_mult={vol_mult} "
            f"min_gap={min_gap} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def vwap_volume_signal(
    price: pd.Series,
    vwap_period: int = 20,
    vol_ma_period: int = 20,
    vol_mult: float = 1.5,
    min_gap: int = 6,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """快捷函数：生成 VWAP 量价信号。"""
    return VWAPVolumeStrategy().generate_signals(
        price,
        vwap_period=vwap_period,
        vol_ma_period=vol_ma_period,
        vol_mult=vol_mult,
        min_gap=min_gap,
    )
