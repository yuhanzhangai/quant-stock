"""标准化回测输出。

每次回测输出统一结构:
data/research/backtests/run_id=xxx/
  config.yml
  metrics.json
  trades.parquet
  equity.parquet
"""

import contextlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl
import vectorbt as vbt
import yaml
from loguru import logger

from src.backtest.metrics import compute_metrics

OUTPUT_DIR = Path("data/research/backtests")
DB_PATH = Path("data/meta/research.duckdb")


def generate_run_id(strategy_name: str, symbol: str, purpose: str = "baseline") -> str:
    """生成人类可读 run_id。

    格式: YYYYMMDD_strategy_symbol_purpose_seq
    """
    date = datetime.now(tz=UTC).strftime("%Y%m%d")
    symbol_short = symbol.replace("-USDT", "").lower()
    seq = uuid.uuid4().hex[:6]
    return f"{date}_{strategy_name}_{symbol_short}_{purpose}_{seq}"


def save_config(
    run_dir: Path,
    run_id: str,
    strategy_name: str,
    strategy_version: str,
    symbol: str,
    timeframe: str,
    params: dict,
    data_version: str = "",
    cost_model: str = "okx_spot",
    **extra: Any,
) -> Path:
    """保存回测配置 YAML。"""
    config = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "symbol": symbol,
        "timeframe": timeframe,
        "params": params,
        "data_version": data_version,
        "cost_model": cost_model,
        "code_commit": _get_git_commit(),
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    config.update(extra)

    path = run_dir / "config.yml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

    return path


def save_metrics(
    run_dir: Path,
    run_id: str,
    strategy_name: str,
    strategy_version: str,
    symbol: str,
    timeframe: str,
    portfolio: vbt.Portfolio,
    initial_cash: float = 50.0,
    leverage: int = 5,
) -> dict:
    """保存标准化 metrics.json。"""
    metrics = compute_metrics(portfolio)

    # Extract trade-level stats
    trades = portfolio.trades.records_readable
    trade_returns = []
    if len(trades) > 0 and "Return" in trades.columns:
        trade_returns = trades["Return"].dropna().tolist()

    avg_win = 0.0
    avg_loss = 0.0
    if trade_returns:
        wins = [r for r in trade_returns if r > 0]
        losses = [r for r in trade_returns if r < 0]
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0

    # Date range from portfolio index
    idx = portfolio.value().index
    start_date = str(idx[0])[:10] if len(idx) > 0 else ""
    end_date = str(idx[-1])[:10] if len(idx) > 0 else ""

    # Calmar ratio
    calmar = 0.0
    ann_return = metrics["total_return"]
    dd = metrics["max_drawdown_pct"] / 100
    if dd > 0:
        calmar = round(ann_return / dd, 4)

    # Consecutive losses + avg holding bars
    max_consec_losses = 0
    avg_holding_bars = 0
    if trade_returns:
        streak = 0
        for r in trade_returns:
            if r < 0:
                streak += 1
                max_consec_losses = max(max_consec_losses, streak)
            else:
                streak = 0
        # Holding bars from trade records
        if "Entry Idx" in trades.columns and "Exit Idx" in trades.columns:
            holding = (trades["Exit Idx"] - trades["Entry Idx"]).dropna()
            avg_holding_bars = int(holding.mean()) if len(holding) > 0 else 0

    # Fee total (from portfolio)
    fee_total = 0.0
    with contextlib.suppress(Exception):
        fee_total = float(portfolio.trades.records_readable.get("Fees Paid", pd.Series([0])).sum())

    n_wins = len([r for r in trade_returns if r > 0])
    wr = n_wins / max(len(trade_returns), 1)

    output = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "symbol": symbol,
        "timeframe": timeframe,
        "start": start_date,
        "end": end_date,
        "initial_cash": initial_cash,
        "leverage": leverage,
        "net_return": round(metrics["total_return"], 6),
        "sharpe": round(metrics["sharpe_ratio"], 4),
        "sortino": round(metrics["sortino_ratio"], 4),
        "calmar": calmar,
        "max_drawdown": round(-metrics["max_drawdown_pct"] / 100, 4),
        "profit_factor": _safe_profit_factor(trade_returns),
        "win_rate": round(wr, 4),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "expectancy": round(avg_win * wr + avg_loss * (1 - wr), 6),
        "trade_count": metrics["total_trades"],
        "avg_holding_bars": avg_holding_bars,
        "max_consecutive_losses": max_consec_losses,
        "fee_total": round(fee_total, 4),
        "slippage_total": 0.0,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }

    path = run_dir / "metrics.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(f"Metrics saved: sharpe={output['sharpe']}, trades={output['trade_count']}")
    return output


def save_trades(run_dir: Path, run_id: str, portfolio: vbt.Portfolio, symbol: str) -> Path | None:
    """保存 trades.parquet。"""
    try:
        trades = portfolio.trades.records_readable
        if len(trades) == 0:
            logger.debug("No trades to save")
            return None

        # Standardize columns
        trade_data = []
        for idx, row in trades.iterrows():
            gross = float(row.get("PnL", 0))
            fee = float(row.get("Fees Paid", 0))
            entry_idx = int(row.get("Entry Idx", 0))
            exit_idx = int(row.get("Exit Idx", 0))
            trade_data.append(
                {
                    "run_id": run_id,
                    "trade_id": idx,
                    "symbol": symbol,
                    "side": "long",
                    "entry_ts": str(row.get("Entry Timestamp", "")),
                    "exit_ts": str(row.get("Exit Timestamp", "")),
                    "entry_price": float(row.get("Avg Entry Price", 0)),
                    "exit_price": float(row.get("Avg Exit Price", 0)),
                    "size": float(row.get("Size", 0)),
                    "gross_pnl": gross,
                    "fee": fee,
                    "slippage": 0.0,
                    "funding_cost": 0.0,
                    "net_pnl": gross - fee,
                    "return_pct": float(row.get("Return", 0)) * 100,
                    "mae_pct": 0.0,  # requires intra-trade tracking
                    "mfe_pct": 0.0,  # requires intra-trade tracking
                    "holding_bars": exit_idx - entry_idx,
                    "exit_reason": str(row.get("Status", "unknown")),
                }
            )

        df = pl.DataFrame(trade_data)
        path = run_dir / "trades.parquet"
        df.write_parquet(str(path))
        logger.debug(f"Trades saved: {len(trade_data)} trades")
        return path
    except Exception as e:
        logger.warning(f"Could not save trades: {e}")
        return None


def save_equity(run_dir: Path, run_id: str, portfolio: vbt.Portfolio) -> Path:
    """保存 equity.parquet。"""
    equity_series = portfolio.value()
    if isinstance(equity_series, pd.DataFrame):
        equity_series = equity_series.iloc[:, 0]

    # Cash and position value
    cash_series = portfolio.cash()
    if isinstance(cash_series, pd.DataFrame):
        cash_series = cash_series.iloc[:, 0]
    position_value = equity_series - cash_series

    eq_df = pd.DataFrame(
        {
            "ts": equity_series.index,
            "equity": equity_series.values,
            "cash": cash_series.values,
            "position_value": position_value.values,
        }
    )

    # Add drawdown
    running_max = equity_series.cummax()
    drawdown = (equity_series - running_max) / running_max
    eq_df["drawdown"] = drawdown.values
    eq_df["gross_exposure"] = position_value.abs().values
    eq_df["net_exposure"] = position_value.values
    eq_df["run_id"] = run_id

    pl_df = pl.from_pandas(eq_df)
    path = run_dir / "equity.parquet"
    pl_df.write_parquet(str(path))
    logger.debug(f"Equity saved: {len(eq_df)} bars")
    return path


def save_to_db(
    metrics: dict,
    run_type: str = "single",
    parent_run_id: str = "",
    output_dir: str = "",
) -> None:
    """写入 research.duckdb backtest_runs 表。"""
    from src.research.db import connect_research_db

    conn = connect_research_db(required=True)
    backtest_id = f"bt_{uuid.uuid4().hex[:12]}"

    conn.execute(
        """
        INSERT INTO backtest_runs
        (backtest_id, run_id, strategy_name, symbol, timeframe,
         initial_cash, net_return, sharpe, sortino, max_drawdown,
         profit_factor, win_rate, expectancy, trade_count,
         avg_trade_return, run_type, parent_run_id, output_dir,
         created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            backtest_id,
            metrics["run_id"],
            metrics["strategy_name"],
            metrics["symbol"],
            metrics["timeframe"],
            metrics["initial_cash"],
            metrics["net_return"],
            metrics["sharpe"],
            metrics["sortino"],
            metrics["max_drawdown"],
            metrics["profit_factor"],
            metrics["win_rate"],
            metrics["expectancy"],
            metrics["trade_count"],
            metrics.get("avg_win", 0),
            run_type,
            parent_run_id,
            output_dir,
            metrics["created_at"],
        ],
    )
    conn.close()
    logger.info(f"Backtest [{run_type}] saved to DB: {backtest_id}")


def save_grid_candidate_to_db(
    parent_run_id: str,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    params: dict,
    metrics: dict,
) -> None:
    """写入参数搜索候选行（轻量，无 artifact 目录）。"""
    import hashlib

    params_hash = hashlib.md5(str(sorted(params.items())).encode()).hexdigest()[:10]
    save_to_db(
        {
            "run_id": f"{parent_run_id}__{params_hash}",
            "strategy_name": strategy_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "initial_cash": metrics.get("init_cash", 50),
            "net_return": metrics.get("total_return", 0),
            "sharpe": metrics.get("sharpe_ratio", 0),
            "sortino": metrics.get("sortino_ratio", 0),
            "max_drawdown": -metrics.get("max_drawdown_pct", 0) / 100,
            "profit_factor": 0,
            "win_rate": metrics.get("win_rate_pct", 0) / 100,
            "expectancy": 0,
            "trade_count": metrics.get("total_trades", 0),
            "avg_win": 0,
            "created_at": datetime.now(tz=UTC).isoformat(),
        },
        run_type="grid_candidate",
        parent_run_id=parent_run_id,
    )


def save_all(
    run_id: str,
    strategy_name: str,
    strategy_version: str,
    symbol: str,
    timeframe: str,
    params: dict,
    portfolio: vbt.Portfolio,
    initial_cash: float = 50.0,
    leverage: int = 5,
    data_version: str = "",
    cost_model: str = "okx_spot",
    run_type: str = "single",
    parent_run_id: str = "",
) -> Path:
    """一键保存所有标准化输出。DB 写入强制执行，不可跳过。"""
    run_dir = OUTPUT_DIR / f"run_id={run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    save_config(
        run_dir,
        run_id,
        strategy_name,
        strategy_version,
        symbol,
        timeframe,
        params,
        data_version,
        cost_model,
    )

    metrics = save_metrics(
        run_dir,
        run_id,
        strategy_name,
        strategy_version,
        symbol,
        timeframe,
        portfolio,
        initial_cash,
        leverage,
    )

    save_trades(run_dir, run_id, portfolio, symbol)
    save_equity(run_dir, run_id, portfolio)

    # DB write is mandatory — no escape hatch
    metrics["timeframe"] = timeframe
    save_to_db(metrics, run_type=run_type, parent_run_id=parent_run_id, output_dir=str(run_dir))

    logger.info(f"Backtest output saved to: {run_dir}")
    return run_dir


def _get_git_commit() -> str:
    """获取当前 git commit hash。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _safe_profit_factor(returns: list[float]) -> float:
    """安全计算 profit factor。"""
    gross_win = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))
    if gross_loss == 0:
        return 0.0
    return round(gross_win / gross_loss, 4)
