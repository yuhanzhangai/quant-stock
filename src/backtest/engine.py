"""基于 vectorbt 的回测引擎封装。"""

from typing import Any, Optional

import numpy as np
import pandas as pd
import vectorbt as vbt
from loguru import logger

from src.backtest.costs import TradingCosts, OKX_SPOT


class BacktestEngine:
    """向量化回测引擎。

    基于 vectorbt 封装，支持多标的同时回测和参数搜索。
    """

    def __init__(
        self,
        costs: TradingCosts = OKX_SPOT,
        init_cash: float = 100_000.0,
        freq: str = "1h",
    ) -> None:
        self._costs = costs
        self._init_cash = init_cash
        self._freq = freq

    def run(
        self,
        price: pd.Series | pd.DataFrame,
        entries: pd.Series | pd.DataFrame,
        exits: pd.Series | pd.DataFrame,
    ) -> vbt.Portfolio:
        """运行回测。

        Args:
            price: 价格序列（index 为时间）
            entries: 入场信号（True/False）
            exits: 出场信号（True/False）

        Returns:
            vectorbt Portfolio 对象
        """
        total_fee = self._costs.total_cost_per_trade

        portfolio = vbt.Portfolio.from_signals(
            close=price,
            entries=entries,
            exits=exits,
            init_cash=self._init_cash,
            fees=total_fee,
            freq=self._freq,
        )

        logger.info(
            f"回测完成 | 初始资金: {self._init_cash:,.0f} | "
            f"手续费率: {total_fee:.4%} | "
            f"最终净值: {portfolio.final_value():,.2f}"
        )
        return portfolio

    def run_grid_search(
        self,
        price: pd.Series,
        signal_func: Any,
        param_grid: dict[str, list],
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """参数网格搜索。

        Args:
            price: 价格序列
            signal_func: 信号生成函数，接受 (price, **params) 返回 (entries, exits)
            param_grid: 参数网格，如 {"short_window": [5, 10], "long_window": [50, 100]}

        Returns:
            (结果 DataFrame, 最优参数字典)
        """
        import itertools

        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(itertools.product(*values))

        results = []

        for combo in combinations:
            params = dict(zip(keys, combo))
            entries, exits = signal_func(price, **params)

            portfolio = self.run(price, entries, exits)

            from src.backtest.metrics import compute_metrics
            metrics = compute_metrics(portfolio)
            metrics.update(params)
            results.append(metrics)

        results_df = pd.DataFrame(results)

        # 找最优（按夏普排序）
        best_idx = results_df["sharpe_ratio"].idxmax()
        best_params = {k: results_df.loc[best_idx, k] for k in keys}

        logger.info(
            f"网格搜索完成 | {len(combinations)} 组合 | "
            f"最优夏普: {results_df.loc[best_idx, 'sharpe_ratio']:.3f} | "
            f"最优参数: {best_params}"
        )

        return results_df, best_params
