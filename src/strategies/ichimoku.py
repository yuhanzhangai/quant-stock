"""一目均衡表 (Ichimoku Cloud) 策略。

经典日本技术分析系统，5 条线判断趋势、支撑、动量。
适合趋势跟踪，在 crypto 4h/1d 上表现较好。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class IchimokuStrategy(StrategyBase):
    """Ichimoku Cloud 策略。

    入场条件（全部满足）：
    1. 转换线 > 基准线（Tenkan > Kijun = 短期动量向上）
    2. 价格在云层上方（强多头区域）
    3. 延迟线（Chikou）在价格上方

    出场条件（任一满足）：
    1. 转换线 < 基准线
    2. 价格跌入云层内部
    """

    @property
    def name(self) -> str:
        return "ichimoku"

    def generate_signals(
        self,
        price: pd.Series,
        tenkan: int = 9,
        kijun: int = 26,
        senkou_b: int = 52,
        use_chikou: bool = False,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成 Ichimoku 信号。"""
        high = price.rolling(2).max()
        low = price.rolling(2).min()

        # 转换线 (Tenkan-sen): (N日最高 + N日最低) / 2
        tenkan_line = (price.rolling(window=tenkan).max() + price.rolling(window=tenkan).min()) / 2

        # 基准线 (Kijun-sen)
        kijun_line = (price.rolling(window=kijun).max() + price.rolling(window=kijun).min()) / 2

        # 先行带 A (Senkou Span A): (转换线 + 基准线) / 2, 前移 kijun 期
        senkou_a = ((tenkan_line + kijun_line) / 2).shift(kijun)

        # 先行带 B (Senkou Span B): (senkou_b日最高 + 最低) / 2, 前移 kijun 期
        senkou_b_line = ((price.rolling(window=senkou_b).max() + price.rolling(window=senkou_b).min()) / 2).shift(kijun)

        # 云层上沿和下沿
        cloud_top = pd.concat([senkou_a, senkou_b_line], axis=1).max(axis=1)
        cloud_bottom = pd.concat([senkou_a, senkou_b_line], axis=1).min(axis=1)

        # 条件
        tk_cross = (tenkan_line > kijun_line) & (tenkan_line.shift(1) <= kijun_line.shift(1))
        above_cloud = price > cloud_top
        below_cloud = price < cloud_bottom

        # 延迟线 (Chikou Span): 收盘价后移 kijun 期
        if use_chikou:
            chikou = price.shift(-kijun)  # 实际用时要小心未来数据
            chikou_ok = price > price.shift(kijun)  # 用历史代替
        else:
            chikou_ok = pd.Series(True, index=price.index)

        # 入场：TK 交叉 + 价格在云上方
        entries = tk_cross & above_cloud & chikou_ok

        # 出场：TK 死叉 或 价格跌入云内
        tk_death = (tenkan_line < kijun_line) & (tenkan_line.shift(1) >= kijun_line.shift(1))
        into_cloud = (price < cloud_top) & (price.shift(1) >= cloud_top.shift(1))
        exits = tk_death | into_cloud

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"Ichimoku | tenkan={tenkan} kijun={kijun} senkou_b={senkou_b} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def ichimoku_signal(
    price: pd.Series, tenkan: int = 9, kijun: int = 26,
    senkou_b: int = 52, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return IchimokuStrategy().generate_signals(
        price, tenkan=tenkan, kijun=kijun, senkou_b=senkou_b
    )
