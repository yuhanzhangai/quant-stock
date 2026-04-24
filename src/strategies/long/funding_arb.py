"""资金费率套利策略（模拟）。

核心：当永续合约资金费率 > 阈值时，做空永续 + 做多现货。
收取资金费率作为收益，同时对冲价格风险。
由于只做研究，这里模拟资金费率收益。

实际交易中：
- 费率 > 0.01% = 做空永续（收费率）
- 费率 < -0.01% = 做多永续（收费率）
- 8 小时结算一次
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class FundingArbStrategy(StrategyBase):
    """资金费率套利。

    入场：资金费率 > entry_threshold 时开始收费
    出场：费率 < exit_threshold 时停止
    收益 = 累计费率 - 手续费
    """

    @property
    def name(self) -> str:
        return "funding_arb"

    def generate_signals(
        self,
        price: pd.Series,
        entry_threshold: float = 0.01,
        exit_threshold: float = 0.005,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """模拟资金费率套利信号。

        由于 price 没有费率数据，用价格动量代理：
        - 价格快速上涨时费率通常为正（多头拥挤）
        - 用 4h 收益率 > 1% 作为"高费率"代理
        """
        # 模拟费率：短期上涨 = 费率可能为正
        ret_4h = price.pct_change(48)  # 48 * 5m = 4h
        ret_1d = price.pct_change(288)  # 288 * 5m = 1d

        # 高费率期间：短期涨幅大 + 长期趋势向上
        high_funding = (ret_4h > 0.01) & (ret_1d > 0.02)

        # 入场：进入高费率区间（做空永续 = 反向持仓）
        # 在高费率时做空永续，所以 price 上涨时"策略"是反向的
        # 对冲后净收益 = 费率 - 基差变化
        entries = high_funding & (~high_funding.shift(1).fillna(False))

        # 出场：费率回落
        low_funding = ret_4h < 0.005
        exits = low_funding & high_funding.shift(1).fillna(False)

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"FundingArb | high_funding_bars:{high_funding.sum()} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def funding_arb_signal(
    price: pd.Series,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return FundingArbStrategy().generate_signals(price)
