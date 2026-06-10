"""仓位管理模块：Kelly 公式 + 波动率调整。

Kelly 公式：f* = (p * b - q) / b
  p = 胜率, q = 败率, b = 盈亏比
  告诉你最优仓位比例

波动率调整：高波动时减仓，低波动时加仓
"""

import numpy as np
import pandas as pd
from loguru import logger


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """计算 Kelly 最优仓位比例。

    Args:
        win_rate: 胜率 (0-1)
        avg_win: 平均盈利幅度
        avg_loss: 平均亏损幅度（正数）

    Returns:
        最优仓位比例 (0-1)，通常用半 Kelly
    """
    if avg_loss == 0 or win_rate == 0:
        return 0.0

    b = avg_win / avg_loss  # 盈亏比
    p = win_rate
    q = 1 - p

    kelly = (p * b - q) / b
    # 限制在 0-1 之间，实践中用半 Kelly
    half_kelly = max(0, min(kelly * 0.5, 1.0))

    logger.debug(f"Kelly | wr:{p:.2f} b:{b:.2f} full:{kelly:.3f} half:{half_kelly:.3f}")
    return half_kelly


def vol_adjusted_size(
    price: pd.Series,
    base_size: float = 1.0,
    target_vol: float = 0.02,  # 目标日波动率 2%
    lookback: int = 20,
) -> pd.Series:
    """波动率调整仓位。

    高波动时减仓，低波动时加仓，保持风险恒定。
    """
    returns = price.pct_change()
    realized_vol = returns.rolling(window=lookback).std()

    # 仓位 = 目标波动率 / 实际波动率
    position_size = target_vol / realized_vol.clip(lower=0.001)
    # 限制在 0.2x - 2x 之间
    position_size = position_size.clip(lower=0.2, upper=2.0) * base_size

    return position_size


def estimate_kelly_from_backtest(returns: pd.Series, entries: pd.Series, exits: pd.Series) -> dict:
    """从回测信号估算 Kelly 参数。"""
    trade_returns = []
    in_trade = False
    entry_price = 0.0

    price_proxy = (1 + returns).cumprod()

    for i in range(len(returns)):
        if entries.iloc[i] and not in_trade:
            entry_price = price_proxy.iloc[i]
            in_trade = True
        elif exits.iloc[i] and in_trade and entry_price > 0:
            trade_ret = (price_proxy.iloc[i] - entry_price) / entry_price
            trade_returns.append(trade_ret)
            in_trade = False

    if len(trade_returns) < 3:
        return {"kelly": 0.0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "n_trades": len(trade_returns)}

    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r <= 0]

    win_rate = len(wins) / len(trade_returns) if trade_returns else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0.001

    k = kelly_fraction(win_rate, avg_win, avg_loss)

    return {
        "kelly": round(k, 3),
        "win_rate": round(win_rate, 3),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "n_trades": len(trade_returns),
        "profit_factor": (
            round(avg_win * win_rate / (avg_loss * (1 - win_rate)), 2) if avg_loss > 0 and win_rate < 1 else 0
        ),
    }
