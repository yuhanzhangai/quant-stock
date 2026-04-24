"""OKX 真实交易成本模型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingCosts:
    """交易成本配置。

    Attributes:
        maker_fee: 挂单手续费率
        taker_fee: 吃单手续费率
        slippage_bps: 滑点（基点）
        fee_multiplier: 费用倍数（压力测试用）
        slippage_multiplier: 滑点倍数（压力测试用）
        funding_enabled: 是否计算资金费
    """

    maker_fee: float = 0.0008  # 0.08%
    taker_fee: float = 0.0010  # 0.10%
    slippage_bps: float = 5.0  # 5 bp
    fee_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    funding_enabled: bool = False

    @property
    def total_cost_per_trade(self) -> float:
        """每笔交易的总成本（吃单 + 滑点）。"""
        fee = self.taker_fee * self.fee_multiplier
        slip = (self.slippage_bps * self.slippage_multiplier) / 10000
        return fee + slip

    @property
    def effective_fee(self) -> float:
        """有效费率。"""
        return self.taker_fee * self.fee_multiplier

    @property
    def effective_slippage(self) -> float:
        """有效滑点。"""
        return (self.slippage_bps * self.slippage_multiplier) / 10000


# 预定义成本模型
OKX_SPOT = TradingCosts(maker_fee=0.0008, taker_fee=0.0010, slippage_bps=5.0)
OKX_SWAP = TradingCosts(maker_fee=0.0002, taker_fee=0.0005, slippage_bps=3.0)
ZERO_COST = TradingCosts(maker_fee=0.0, taker_fee=0.0, slippage_bps=0.0)

# 压力测试模型
OKX_SWAP_BASE = TradingCosts(
    maker_fee=0.0002,
    taker_fee=0.0005,
    slippage_bps=3.0,
    fee_multiplier=1.0,
    slippage_multiplier=1.0,
    funding_enabled=True,
)
OKX_SWAP_STRESS = TradingCosts(
    maker_fee=0.0002,
    taker_fee=0.0005,
    slippage_bps=3.0,
    fee_multiplier=1.5,
    slippage_multiplier=2.0,
    funding_enabled=True,
)
OKX_SPOT_STRESS = TradingCosts(
    maker_fee=0.0008,
    taker_fee=0.0010,
    slippage_bps=5.0,
    fee_multiplier=1.5,
    slippage_multiplier=2.0,
)
