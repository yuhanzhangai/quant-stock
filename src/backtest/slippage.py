"""滑点模型。

提供多种滑点估算方式，从简单到复杂：
- fixed_bps: 固定基点滑点
- atr_based: 基于 ATR 的动态滑点
- stress: 压力场景滑点
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class SlippageEstimate:
    """滑点估算结果。"""

    model: str
    avg_slippage_bps: float
    total_slippage_cost: float
    per_trade_slippage: list[float]


def fixed_bps_slippage(
    n_trades: int,
    notional_per_trade: float,
    bps: float = 5.0,
) -> SlippageEstimate:
    """固定基点滑点模型。

    Args:
        n_trades: 交易次数
        notional_per_trade: 每笔名义金额
        bps: 滑点基点 (default 5bp = 0.05%)
    """
    slip_per_trade = notional_per_trade * bps / 10000
    total = slip_per_trade * n_trades

    return SlippageEstimate(
        model="fixed_bps",
        avg_slippage_bps=bps,
        total_slippage_cost=round(total, 4),
        per_trade_slippage=[round(slip_per_trade, 4)] * n_trades,
    )


def atr_based_slippage(
    price: pd.Series,
    entry_indices: list[int],
    atr_period: int = 14,
    atr_fraction: float = 0.1,
) -> SlippageEstimate:
    """基于 ATR 的动态滑点。

    滑点 = ATR * atr_fraction（波动越大滑点越大）。
    """
    # Calculate ATR-like measure from close price
    returns = price.pct_change().abs()
    atr_proxy = returns.rolling(atr_period).mean()

    slippages = []
    for idx in entry_indices:
        if idx < len(atr_proxy) and pd.notna(atr_proxy.iloc[idx]):
            slip = float(atr_proxy.iloc[idx]) * atr_fraction
        else:
            slip = 0.0005  # default 5bp
        slippages.append(round(slip * 10000, 2))  # convert to bps

    avg_bps = sum(slippages) / max(len(slippages), 1)

    return SlippageEstimate(
        model="atr_based",
        avg_slippage_bps=round(avg_bps, 2),
        total_slippage_cost=0.0,  # depends on notional
        per_trade_slippage=slippages,
    )


def stress_slippage(
    n_trades: int,
    notional_per_trade: float,
    normal_bps: float = 5.0,
    stress_multiplier: float = 3.0,
) -> SlippageEstimate:
    """压力场景滑点：正常滑点 × 倍数。"""
    stress_bps = normal_bps * stress_multiplier
    slip_per_trade = notional_per_trade * stress_bps / 10000
    total = slip_per_trade * n_trades

    return SlippageEstimate(
        model="stress",
        avg_slippage_bps=stress_bps,
        total_slippage_cost=round(total, 4),
        per_trade_slippage=[round(slip_per_trade, 4)] * n_trades,
    )


def estimate_funding_cost(
    notional: float,
    holding_hours: float,
    avg_funding_rate: float = 0.0001,
    funding_intervals_per_day: int = 3,
) -> float:
    """估算资金费用。

    funding_cost = notional × avg_rate × holding_days × intervals_per_day
    """
    holding_days = holding_hours / 24
    cost = notional * avg_funding_rate * holding_days * funding_intervals_per_day
    return round(cost, 4)
