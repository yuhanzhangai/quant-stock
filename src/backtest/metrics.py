"""回测指标计算。"""

from typing import Any

import numpy as np
import vectorbt as vbt


def compute_metrics(portfolio: vbt.Portfolio) -> dict[str, Any]:
    """从 Portfolio 中提取标准回测指标。

    Args:
        portfolio: vectorbt Portfolio 对象

    Returns:
        指标字典
    """
    stats = portfolio.stats()

    total_return = portfolio.total_return()
    if isinstance(total_return, (int, float, np.floating)):
        total_return_val = float(total_return)
    else:
        total_return_val = float(total_return.iloc[0]) if len(total_return) > 0 else 0.0

    # 安全提取标量值
    def safe_float(val: Any, default: float = 0.0) -> float:
        if val is None:
            return default
        if isinstance(val, (int, float, np.floating)):
            return float(val)
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    sharpe = safe_float(stats.get("Sharpe Ratio", 0.0))
    sortino = safe_float(stats.get("Sortino Ratio", 0.0))
    max_dd = safe_float(stats.get("Max Drawdown [%]", 0.0))
    win_rate = safe_float(stats.get("Win Rate [%]", 0.0))
    total_trades = safe_float(stats.get("Total Trades", 0))

    return {
        "total_return": total_return_val,
        "total_return_pct": total_return_val * 100,
        "final_value": safe_float(portfolio.final_value()),
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": max_dd,
        "win_rate_pct": win_rate,
        "total_trades": int(total_trades),
        "init_cash": safe_float(portfolio.init_cash),
    }
