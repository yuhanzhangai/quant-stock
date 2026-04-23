"""OKX 真实交易成本模型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingCosts:
    """交易成本配置。

    Attributes:
        maker_fee: 挂单手续费率
        taker_fee: 吃单手续费率
        slippage_bps: 滑点（基点）
    """

    maker_fee: float = 0.0008  # 0.08%
    taker_fee: float = 0.0010  # 0.10%
    slippage_bps: float = 5.0  # 5 bp

    @property
    def total_cost_per_trade(self) -> float:
        """每笔交易的总成本（吃单 + 滑点）。"""
        return self.taker_fee + self.slippage_bps / 10000


# 预定义成本模型
OKX_SPOT = TradingCosts(maker_fee=0.0008, taker_fee=0.0010, slippage_bps=5.0)
OKX_SWAP = TradingCosts(maker_fee=0.0002, taker_fee=0.0005, slippage_bps=3.0)
ZERO_COST = TradingCosts(maker_fee=0.0, taker_fee=0.0, slippage_bps=0.0)
