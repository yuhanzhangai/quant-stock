"""Portfolio-constrained replay with artifact persistence + DB writes."""

import json
import uuid
from pathlib import Path

import pandas as pd
import polars as pl
from loguru import logger

from src.backtest.costs import OKX_SWAP, OKX_SWAP_STRESS
from src.replay.exit_modes import EXIT_MODE_FUNCS, get_coin_exit_config
from src.research.db import connect_research_db
from src.risk.risk_engine import RiskEngine


def run_portfolio_replay(
    entries: pl.DataFrame,
    price: pd.Series,
    symbol: str,
    exit_modes: list[str] | None = None,
    cost_model_name: str = "normal",
) -> dict[str, dict]:
    """Run portfolio-constrained replay for all exit modes.

    Returns dict of {mode_name: {trades, equity_curve, metrics, rejected}}.
    """
    if exit_modes is None:
        exit_modes = list(EXIT_MODE_FUNCS.keys())

    cost = OKX_SWAP if cost_model_name == "normal" else OKX_SWAP_STRESS
    results = {}

    for mode_name in exit_modes:
        exit_func = EXIT_MODE_FUNCS[mode_name]
        coin_config = get_coin_exit_config(symbol)
        r = _run_single_portfolio(entries, price, symbol, mode_name, exit_func, coin_config, cost)
        results[mode_name] = r
        logger.info(
            f"  {mode_name} [{cost_model_name}]: trades={r['metrics']['trade_count']} "
            f"return={r['metrics']['net_return']:.4f} pf={r['metrics']['profit_factor']:.4f}"
        )

    return results


def _run_single_portfolio(entries, price, symbol, mode_name, exit_func, coin_config, cost):
    """Run one exit_mode with portfolio constraints."""
    risk = RiskEngine()
    equity = 50.0
    fee_rate = cost.total_cost_per_trade
    trades = []
    rejected = []
    equity_curve = [{"bar": 0, "equity": equity}]
    in_trade = False

    for row in entries.iter_rows(named=True):
        entry_idx = row["entry_bar_idx"]
        entry_price = row["entry_price"]

        if in_trade:
            rejected.append({"entry_id": row["entry_id"], "reason": "already_in_position"})
            continue

        dec = risk.check_signal(bar_idx=entry_idx, expected_edge=0.005, cost_per_trade=fee_rate)
        if not dec.accepted:
            rejected.append({"entry_id": row["entry_id"], "reason": dec.reason})
            continue

        if mode_name == "current_exit":
            exit_idx, exit_price, exit_reason = exit_func(
                price,
                entry_idx,
                entry_price,
                trail=coin_config["trail"],
                trail_pct=coin_config["trail_pct"],
                take_profit_pct=coin_config["take_profit_pct"],
            )
        else:
            exit_idx, exit_price, exit_reason = exit_func(price, entry_idx, entry_price)

        pnl_pct = (exit_price - entry_price) / entry_price * 100
        gross = equity * pnl_pct / 100
        net = gross - fee_rate * 2 * equity
        equity += net
        risk.update_trade_result(net, exit_idx)

        trades.append(
            {
                "entry_id": row["entry_id"],
                "symbol": symbol,
                "entry_ts": row["entry_ts"],
                "entry_price": entry_price,
                "exit_bar_idx": exit_idx,
                "exit_price": exit_price,
                "exit_mode": mode_name,
                "exit_reason": exit_reason,
                "return_pct": round(pnl_pct, 6),
                "net_pnl": round(net, 4),
                "fee": round(fee_rate * 2 * equity, 4),
                "holding_bars": exit_idx - entry_idx,
            }
        )
        equity_curve.append({"bar": exit_idx, "equity": round(equity, 4)})

    # Compute metrics
    wins = [t["return_pct"] for t in trades if t["return_pct"] > 0]
    losses = [t["return_pct"] for t in trades if t["return_pct"] <= 0]
    max_eq = 50.0
    max_dd = 0.0
    eq = 50.0
    for t in trades:
        eq += t["net_pnl"]
        max_eq = max(max_eq, eq)
        dd = (eq - max_eq) / max_eq if max_eq > 0 else 0
        max_dd = min(max_dd, dd)

    metrics = {
        "exit_mode": mode_name,
        "symbol": symbol,
        "trade_count": len(trades),
        "rejected_count": len(rejected),
        "final_equity": round(equity, 2),
        "net_return": round((equity - 50) / 50, 4),
        "win_rate": round(len(wins) / max(len(trades), 1), 4),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else 0.0,
        "max_drawdown": round(max_dd, 4),
        "avg_holding_bars": round(sum(t["holding_bars"] for t in trades) / max(len(trades), 1)),
        "fee_to_gross": round(
            (50 * cost.total_cost_per_trade * 2 * len(trades))
            / max(abs(sum(t["return_pct"] * 50 / 100 for t in trades)), 0.01),
            4,
        ),
    }

    return {"trades": trades, "equity_curve": equity_curve, "metrics": metrics, "rejected": rejected}


def save_portfolio_replay(
    results: dict[str, dict],
    run_dir: Path,
    parent_run_id: str = "",
    cost_model_name: str = "normal",
) -> None:
    """Save portfolio replay artifacts + write backtest_runs."""
    for mode_name, data in results.items():
        mode_dir = run_dir / "portfolio" / f"exit_mode={mode_name}"
        mode_dir.mkdir(parents=True, exist_ok=True)

        pl.DataFrame(data["trades"]).write_parquet(str(mode_dir / "trades.parquet"))
        pl.DataFrame(data["equity_curve"]).write_parquet(str(mode_dir / "equity.parquet"))
        pl.DataFrame(data["rejected"]).write_parquet(str(mode_dir / "rejected_entries.parquet"))

        with open(mode_dir / "metrics.json", "w", encoding="utf-8") as f:
            json.dump(data["metrics"], f, indent=2)

        # Write backtest_runs
        _write_backtest_run(data["metrics"], parent_run_id, str(mode_dir), cost_model_name)

    logger.info(f"Portfolio replay saved: {run_dir / 'portfolio'}")


def save_windowed_summary(
    entries: pl.DataFrame,
    price: pd.Series,
    symbol: str,
    run_dir: Path,
) -> pl.DataFrame:
    """Run windowed replay using timestamp-based windows and save summary."""
    # Get timestamp range
    ts_index = price.index
    end_ts = ts_index[-1]

    windows = {
        "full_sample": (ts_index[0], end_ts),
        "recent_90d": (end_ts - pd.Timedelta(days=90), end_ts),
        "recent_60d": (end_ts - pd.Timedelta(days=60), end_ts),
        "recent_30d": (end_ts - pd.Timedelta(days=30), end_ts),
    }

    rows = []
    for wname, (w_start, w_end) in windows.items():
        # Filter entries by timestamp
        entry_ts_list = entries["entry_ts"].to_list()
        mask = [(w_start <= pd.Timestamp(ts) <= w_end) for ts in entry_ts_list]
        w_entries = entries.filter(pl.Series(mask))

        if len(w_entries) == 0:
            continue

        result = run_portfolio_replay(w_entries, price, symbol)
        for mode_name, data in result.items():
            m = data["metrics"]
            rows.append(
                {
                    "window": wname,
                    "exit_mode": mode_name,
                    "trade_count": m["trade_count"],
                    "net_return": m["net_return"],
                    "profit_factor": m["profit_factor"],
                    "max_drawdown": m["max_drawdown"],
                    "fee_to_gross": m["fee_to_gross"],
                    "avg_holding_bars": m["avg_holding_bars"],
                }
            )

    summary = pl.DataFrame(rows)
    summary_dir = run_dir / "windows"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary.write_csv(str(summary_dir / "windowed_summary.csv"))
    with open(summary_dir / "windowed_summary.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    logger.info(f"Windowed summary saved: {summary_dir}")
    return summary


def save_cost_stress_summary(
    entries: pl.DataFrame,
    price: pd.Series,
    symbol: str,
    run_dir: Path,
) -> pl.DataFrame:
    """Run cost stress test and save summary."""
    rows = []
    for cost_name in ["normal", "pessimistic"]:
        result = run_portfolio_replay(entries, price, symbol, cost_model_name=cost_name)
        for mode_name, data in result.items():
            m = data["metrics"]
            rows.append(
                {
                    "exit_mode": mode_name,
                    "cost_scenario": cost_name,
                    "trade_count": m["trade_count"],
                    "net_return": m["net_return"],
                    "profit_factor": m["profit_factor"],
                    "max_drawdown": m["max_drawdown"],
                    "fee_to_gross": m["fee_to_gross"],
                    "survives_cost_stress": m["final_equity"] > 40,
                }
            )

    summary = pl.DataFrame(rows)
    stress_dir = run_dir / "cost_stress"
    stress_dir.mkdir(parents=True, exist_ok=True)
    summary.write_csv(str(stress_dir / "cost_stress_summary.csv"))
    with open(stress_dir / "cost_stress_summary.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    logger.info(f"Cost stress summary saved: {stress_dir}")
    return summary


def _write_backtest_run(metrics: dict, parent_run_id: str, output_dir: str, cost_model: str) -> None:
    """Write to backtest_runs table."""
    import subprocess

    conn = connect_research_db(required=True)
    bt_id = f"bt_{uuid.uuid4().hex[:12]}"
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        commit = "unknown"

    conn.execute(
        """
        INSERT INTO backtest_runs
        (backtest_id, run_id, strategy_name, symbol, timeframe,
         net_return, sharpe, profit_factor, win_rate, max_drawdown,
         trade_count, run_type, parent_run_id, output_dir, code_commit, created_at)
        VALUES (?, ?, 'minswing_v3', ?, '5m', ?, 0, ?, ?, ?, ?,
                'exit_mode_portfolio_replay', ?, ?, ?, current_timestamp)
        """,
        [
            bt_id,
            f"{parent_run_id}__{metrics['exit_mode']}__{cost_model}",
            metrics["symbol"],
            metrics["net_return"],
            metrics["profit_factor"],
            metrics["win_rate"],
            metrics["max_drawdown"],
            metrics["trade_count"],
            parent_run_id,
            output_dir,
            commit,
        ],
    )
    conn.close()
