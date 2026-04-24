"""Validation gates — 每个 gate 输出统一格式。

Gate 输出:
{
    "gate_name": str,
    "status": "pass" | "fail" | "warning" | "skipped" | "error",
    "score": float,
    "threshold": float,
    "details": dict
}
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import vectorbt as vbt

from src.backtest.costs import OKX_SPOT, TradingCosts
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics


@dataclass
class GateResult:
    """Gate 验证结果。"""

    gate_name: str
    status: str  # pass | fail | warning | skipped | error
    score: float = 0.0
    threshold: float = 0.0
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转为字典。"""
        return {
            "gate_name": self.gate_name,
            "status": self.status,
            "score": self.score,
            "threshold": self.threshold,
            "details": self.details,
        }


# ── Gate 1: Data Quality ──


def gate_data_quality(df: Any, timeframe: str) -> GateResult:
    """数据质量门禁：critical issue = 0 才通过。"""

    from src.data_quality.checks import has_critical_failure, run_all_checks

    results = run_all_checks(df, timeframe=timeframe)
    critical_count = sum(1 for r in results if r.status == "fail" and r.severity == "critical")
    warning_count = sum(1 for r in results if r.status == "warning")

    status = "fail" if has_critical_failure(results) else "pass"
    return GateResult(
        "data_quality",
        status,
        score=critical_count,
        threshold=0,
        details={"critical_issues": critical_count, "warnings": warning_count},
    )


# ── Gate 2: Baseline Backtest ──


def gate_baseline_backtest(
    price: pd.Series,
    signal_func: Any,
    params: dict,
    min_trades: int = 30,
    min_profit_factor: float = 1.1,
    max_drawdown: float = -0.30,
) -> GateResult:
    """基础回测门禁：策略不能是明显垃圾。"""
    try:
        entries, exits = signal_func(price, **params)
        engine = BacktestEngine(costs=OKX_SPOT, init_cash=50, freq="5min")
        portfolio = engine.run(price, entries, exits)
        metrics = compute_metrics(portfolio)

        trades = metrics["total_trades"]
        pf = _calc_profit_factor(portfolio)
        dd = -metrics["max_drawdown_pct"] / 100

        issues = []
        if trades < min_trades:
            issues.append(f"trades={trades} < {min_trades}")
        if pf < min_profit_factor:
            issues.append(f"pf={pf:.2f} < {min_profit_factor}")
        if dd < max_drawdown:
            issues.append(f"dd={dd:.2f} < {max_drawdown}")

        status = "fail" if issues else "pass"
        return GateResult(
            "baseline_backtest",
            status,
            score=pf,
            threshold=min_profit_factor,
            details={
                "trade_count": trades,
                "profit_factor": round(pf, 4),
                "max_drawdown": round(dd, 4),
                "sharpe": round(metrics["sharpe_ratio"], 4),
                "issues": issues,
            },
        )
    except Exception as e:
        return GateResult("baseline_backtest", "error", details={"error": str(e)})


# ── Gate 3: Cost Stress ──


def gate_cost_stress(
    price: pd.Series,
    signal_func: Any,
    params: dict,
    normal_pf_threshold: float = 1.25,
    pessimistic_pf_threshold: float = 1.0,
) -> GateResult:
    """成本压力测试：normal 必须通过，pessimistic 不能崩溃。"""
    try:
        entries, exits = signal_func(price, **params)
        results = {}

        for label, fee_mult, _slip_bps in [
            ("optimistic", 1.0, 0.0002),
            ("normal", 1.0, 0.0005),
            ("pessimistic", 1.5, 0.001),
        ]:
            costs = TradingCosts(
                maker_fee=OKX_SPOT.maker_fee * fee_mult,
                taker_fee=OKX_SPOT.taker_fee * fee_mult,
            )
            engine = BacktestEngine(costs=costs, init_cash=50, freq="5min")
            portfolio = engine.run(price, entries, exits)
            pf = _calc_profit_factor(portfolio)
            metrics = compute_metrics(portfolio)
            results[label] = {
                "profit_factor": round(pf, 4),
                "sharpe": round(metrics["sharpe_ratio"], 4),
                "return": round(metrics["total_return"], 4),
            }

        normal_pf = results["normal"]["profit_factor"]
        pessimistic_pf = results["pessimistic"]["profit_factor"]

        issues = []
        if normal_pf < normal_pf_threshold:
            issues.append(f"normal_pf={normal_pf:.2f} < {normal_pf_threshold}")
        if pessimistic_pf < pessimistic_pf_threshold:
            issues.append(f"pessimistic_pf={pessimistic_pf:.2f} < {pessimistic_pf_threshold}")

        status = "fail" if issues else "pass"
        return GateResult(
            "cost_stress",
            status,
            score=normal_pf,
            threshold=normal_pf_threshold,
            details={"scenarios": results, "issues": issues},
        )
    except Exception as e:
        return GateResult("cost_stress", "error", details={"error": str(e)})


# ── Gate 4: Out of Sample ──


def gate_oos(
    price: pd.Series,
    signal_func: Any,
    params: dict,
    train_ratio: float = 0.7,
    min_pf: float = 1.25,
    min_trades: int = 30,
    max_dd: float = -0.25,
) -> GateResult:
    """样本外验证：在未见数据上保持有效。"""
    try:
        split = int(len(price) * train_ratio)
        test_price = price.iloc[split:]

        if len(test_price) < 200:
            return GateResult("oos", "skipped", details={"reason": "insufficient test data"})

        entries, exits = signal_func(test_price, **params)
        engine = BacktestEngine(costs=OKX_SPOT, init_cash=50, freq="5min")
        portfolio = engine.run(test_price, entries, exits)
        metrics = compute_metrics(portfolio)

        pf = _calc_profit_factor(portfolio)
        trades = metrics["total_trades"]
        dd = -metrics["max_drawdown_pct"] / 100

        issues = []
        if pf < min_pf:
            issues.append(f"oos_pf={pf:.2f} < {min_pf}")
        if trades < min_trades:
            issues.append(f"oos_trades={trades} < {min_trades}")
        if dd < max_dd:
            issues.append(f"oos_dd={dd:.2f} < {max_dd}")

        status = "fail" if issues else "pass"
        return GateResult(
            "oos",
            status,
            score=pf,
            threshold=min_pf,
            details={
                "profit_factor": round(pf, 4),
                "sharpe": round(metrics["sharpe_ratio"], 4),
                "trade_count": trades,
                "max_drawdown": round(dd, 4),
                "test_bars": len(test_price),
                "issues": issues,
            },
        )
    except Exception as e:
        return GateResult("oos", "error", details={"error": str(e)})


# ── Gate 5: Walk Forward ──


def gate_walk_forward(
    price: pd.Series,
    signal_func: Any,
    params: dict,
    n_windows: int = 5,
    min_positive_ratio: float = 0.60,
) -> GateResult:
    """前推验证：多数窗口能活下来。"""
    try:
        window_size = len(price) // (n_windows + 1)
        if window_size < 200:
            return GateResult("walk_forward", "skipped", details={"reason": "insufficient data for windows"})

        positive_windows = 0
        window_results = []

        for i in range(n_windows):
            start = (i + 1) * window_size
            end = start + window_size
            if end > len(price):
                break
            w_price = price.iloc[start:end]
            entries, exits = signal_func(w_price, **params)
            engine = BacktestEngine(costs=OKX_SPOT, init_cash=50, freq="5min")
            portfolio = engine.run(w_price, entries, exits)
            metrics = compute_metrics(portfolio)
            pf = _calc_profit_factor(portfolio)

            is_positive = metrics["total_return"] > 0
            if is_positive:
                positive_windows += 1
            window_results.append(
                {
                    "window": i + 1,
                    "return": round(metrics["total_return"], 4),
                    "sharpe": round(metrics["sharpe_ratio"], 4),
                    "profit_factor": round(pf, 4),
                    "positive": is_positive,
                }
            )

        total_windows = len(window_results)
        ratio = positive_windows / max(total_windows, 1)

        status = "fail" if ratio < min_positive_ratio else "pass"
        return GateResult(
            "walk_forward",
            status,
            score=round(ratio, 4),
            threshold=min_positive_ratio,
            details={
                "positive_windows": positive_windows,
                "total_windows": total_windows,
                "ratio": round(ratio, 4),
                "windows": window_results,
            },
        )
    except Exception as e:
        return GateResult("walk_forward", "error", details={"error": str(e)})


# ── Gate 6: Random Baseline ──


def gate_random_baseline(
    price: pd.Series,
    signal_func: Any,
    params: dict,
    n_random: int = 100,
) -> GateResult:
    """随机基线：策略至少打败随机的 P75。"""
    try:
        # Strategy performance
        entries, exits = signal_func(price, **params)
        engine = BacktestEngine(costs=OKX_SPOT, init_cash=50, freq="5min")
        portfolio = engine.run(price, entries, exits)
        strat_return = compute_metrics(portfolio)["total_return"]
        strat_pf = _calc_profit_factor(portfolio)

        # Count actual trade frequency
        n_trades = int(entries.sum())
        if n_trades == 0:
            return GateResult("random_baseline", "skipped", details={"reason": "no trades"})

        # Random simulations
        random_returns = []
        rng = np.random.default_rng(42)
        for _ in range(n_random):
            rand_entries = pd.Series(False, index=price.index)
            rand_indices = rng.choice(len(price) - 50, size=min(n_trades, len(price) // 50), replace=False)
            rand_entries.iloc[rand_indices] = True
            rand_exits = rand_entries.shift(20).fillna(False)
            rp = engine.run(price, rand_entries, rand_exits)
            random_returns.append(compute_metrics(rp)["total_return"])

        p75 = float(np.percentile(random_returns, 75))

        status = "pass" if strat_return > p75 else "fail"
        return GateResult(
            "random_baseline",
            status,
            score=round(strat_return, 4),
            threshold=round(p75, 4),
            details={
                "strategy_return": round(strat_return, 4),
                "strategy_pf": round(strat_pf, 4),
                "random_p25": round(float(np.percentile(random_returns, 25)), 4),
                "random_p50": round(float(np.percentile(random_returns, 50)), 4),
                "random_p75": round(p75, 4),
                "n_simulations": n_random,
            },
        )
    except Exception as e:
        return GateResult("random_baseline", "error", details={"error": str(e)})


# ── Gate 7: Monte Carlo ──


def gate_monte_carlo(
    price: pd.Series,
    signal_func: Any,
    params: dict,
    n_sims: int = 500,
    min_profit_prob: float = 0.60,
) -> GateResult:
    """蒙特卡洛：盈利概率和尾部风险。"""
    try:
        entries, exits = signal_func(price, **params)
        engine = BacktestEngine(costs=OKX_SPOT, init_cash=50, freq="5min")
        portfolio = engine.run(price, entries, exits)

        trades = portfolio.trades.records_readable
        if len(trades) == 0 or "Return" not in trades.columns:
            return GateResult("monte_carlo", "skipped", details={"reason": "no trades"})

        trade_returns = trades["Return"].dropna().values

        rng = np.random.default_rng(42)
        sim_finals = []
        for _ in range(n_sims):
            shuffled = rng.choice(trade_returns, size=len(trade_returns), replace=True)
            equity = 50.0
            for r in shuffled:
                equity *= 1 + r
            sim_finals.append(equity)

        sim_finals = np.array(sim_finals)
        profit_prob = float((sim_finals > 50).mean())
        worst_5 = float(np.percentile(sim_finals, 5))
        median_final = float(np.median(sim_finals))

        status = "pass" if profit_prob >= min_profit_prob else "fail"
        return GateResult(
            "monte_carlo",
            status,
            score=round(profit_prob, 4),
            threshold=min_profit_prob,
            details={
                "profit_probability": round(profit_prob, 4),
                "median_final_equity": round(median_final, 2),
                "worst_5pct": round(worst_5, 2),
                "best_5pct": round(float(np.percentile(sim_finals, 95)), 2),
                "n_simulations": n_sims,
            },
        )
    except Exception as e:
        return GateResult("monte_carlo", "error", details={"error": str(e)})


# ── Gate 8: Event Backtest ──


def gate_event_backtest(
    price: pd.Series,
    signal_func: Any,
    params: dict,
    max_event_dd: float = -0.30,
) -> GateResult:
    """重大事件测试：不能出现系统性失控。"""
    try:
        entries, exits = signal_func(price, **params)
        engine = BacktestEngine(costs=OKX_SPOT, init_cash=50, freq="5min")
        portfolio = engine.run(price, entries, exits)
        metrics = compute_metrics(portfolio)

        dd = -metrics["max_drawdown_pct"] / 100

        status = "pass" if dd > max_event_dd else "warning"
        return GateResult(
            "event_backtest",
            status,
            score=round(dd, 4),
            threshold=max_event_dd,
            details={
                "max_drawdown": round(dd, 4),
                "note": "Full-period backtest used as proxy for event stress",
            },
        )
    except Exception as e:
        return GateResult("event_backtest", "error", details={"error": str(e)})


# ── Gate 9: Parameter Stability ──


def gate_parameter_stability(
    price: pd.Series,
    signal_func: Any,
    base_params: dict,
    param_name: str,
    variations: list,
    min_stable_ratio: float = 0.5,
) -> GateResult:
    """参数稳定性：最优参数附近存在可接受区域。"""
    try:
        engine = BacktestEngine(costs=OKX_SPOT, init_cash=50, freq="5min")
        results = []

        for val in variations:
            test_params = {**base_params, param_name: val}
            entries, exits = signal_func(price, **test_params)
            portfolio = engine.run(price, entries, exits)
            metrics = compute_metrics(portfolio)
            results.append(
                {
                    param_name: val,
                    "sharpe": round(metrics["sharpe_ratio"], 4),
                    "return": round(metrics["total_return"], 4),
                    "profitable": metrics["total_return"] > 0,
                }
            )

        profitable = sum(1 for r in results if r["profitable"])
        ratio = profitable / max(len(results), 1)

        status = "pass" if ratio >= min_stable_ratio else "fail"
        return GateResult(
            "parameter_stability",
            status,
            score=round(ratio, 4),
            threshold=min_stable_ratio,
            details={
                "param_tested": param_name,
                "profitable_variations": profitable,
                "total_variations": len(results),
                "ratio": round(ratio, 4),
                "results": results,
            },
        )
    except Exception as e:
        return GateResult("parameter_stability", "error", details={"error": str(e)})


# ── Helpers ──


def _calc_profit_factor(portfolio: vbt.Portfolio) -> float:
    """安全计算 profit factor。"""
    try:
        trades = portfolio.trades.records_readable
        if len(trades) == 0 or "Return" not in trades.columns:
            return 0.0
        returns = trades["Return"].dropna()
        gross_win = returns[returns > 0].sum()
        gross_loss = abs(returns[returns < 0].sum())
        return round(float(gross_win / gross_loss), 4) if gross_loss > 0 else 0.0
    except Exception:
        return 0.0
