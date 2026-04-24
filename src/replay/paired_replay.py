"""Entry-level paired replay — 同一批 entry，不同 exit_mode 逐笔比较。"""

from pathlib import Path

import pandas as pd
import polars as pl
from loguru import logger

from src.replay.exit_modes import EXIT_MODE_FUNCS


def run_paired_replay(
    common_entries: pl.DataFrame,
    price: pd.Series,
    exit_modes: list[str] | None = None,
) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """对同一批 entry 分别运行不同 exit_mode。

    Args:
        common_entries: common_entries.parquet 内容
        price: 完整价格序列 (datetime index)
        exit_modes: 要运行的 exit_mode 列表

    Returns:
        (per_mode_trades, paired_comparison)
    """
    if exit_modes is None:
        exit_modes = list(EXIT_MODE_FUNCS.keys())

    per_mode_trades: dict[str, pl.DataFrame] = {}

    for mode_name in exit_modes:
        func = EXIT_MODE_FUNCS[mode_name]
        trades = _run_single_exit_mode(common_entries, price, mode_name, func)
        per_mode_trades[mode_name] = trades
        logger.info(f"  {mode_name}: {len(trades)} trades")

    # Build paired comparison
    comparison = _build_paired_comparison(common_entries, per_mode_trades, exit_modes)
    logger.info(f"Paired comparison: {len(comparison)} entries")

    return per_mode_trades, comparison


def _run_single_exit_mode(
    entries: pl.DataFrame,
    price: pd.Series,
    mode_name: str,
    exit_func: callable,
) -> pl.DataFrame:
    """对所有 entry 运行单个 exit_mode。"""
    trades = []

    for row in entries.iter_rows(named=True):
        entry_idx = row["entry_bar_idx"]
        entry_price = row["entry_price"]
        entry_id = row["entry_id"]

        exit_idx, exit_price, exit_reason = exit_func(
            price,
            entry_idx,
            entry_price,
        )

        pnl_pct = (exit_price - entry_price) / entry_price * 100

        # MAE/MFE
        if exit_idx > entry_idx:
            segment = price.iloc[entry_idx : exit_idx + 1]
            mae = (float(segment.min()) - entry_price) / entry_price * 100
            mfe = (float(segment.max()) - entry_price) / entry_price * 100
        else:
            mae = 0.0
            mfe = 0.0

        trades.append(
            {
                "entry_id": entry_id,
                "symbol": row["symbol"],
                "entry_ts": row["entry_ts"],
                "entry_price": entry_price,
                "exit_bar_idx": exit_idx,
                "exit_price": exit_price,
                "exit_mode": mode_name,
                "exit_reason": exit_reason,
                "return_pct": round(pnl_pct, 6),
                "mae_pct": round(mae, 6),
                "mfe_pct": round(mfe, 6),
                "holding_bars": exit_idx - entry_idx,
            }
        )

    return pl.DataFrame(trades)


def _build_paired_comparison(
    entries: pl.DataFrame,
    per_mode: dict[str, pl.DataFrame],
    exit_modes: list[str],
) -> pl.DataFrame:
    """构建逐 entry 配对比较表。"""
    rows = []
    entry_ids = entries["entry_id"].to_list()

    # Index trades by entry_id per mode
    indexed: dict[str, dict[str, dict]] = {}
    for mode, trades_df in per_mode.items():
        indexed[mode] = {}
        for row in trades_df.iter_rows(named=True):
            indexed[mode][row["entry_id"]] = row

    for eid in entry_ids:
        entry_row = entries.filter(pl.col("entry_id") == eid).row(0, named=True)
        comp: dict = {
            "entry_id": eid,
            "symbol": entry_row["symbol"],
            "entry_ts": entry_row["entry_ts"],
        }

        returns = {}
        for mode in exit_modes:
            trade = indexed.get(mode, {}).get(eid)
            if trade:
                comp[f"{mode}_return"] = trade["return_pct"]
                comp[f"{mode}_reason"] = trade["exit_reason"]
                comp[f"{mode}_holding_bars"] = trade["holding_bars"]
                comp[f"{mode}_mae"] = trade["mae_pct"]
                comp[f"{mode}_mfe"] = trade["mfe_pct"]
                returns[mode] = trade["return_pct"]
            else:
                comp[f"{mode}_return"] = None
                comp[f"{mode}_reason"] = "no_trade"
                comp[f"{mode}_holding_bars"] = None
                comp[f"{mode}_mae"] = None
                comp[f"{mode}_mfe"] = None

        # Best/worst
        if returns:
            comp["best_exit_mode"] = max(returns, key=returns.get)
            comp["worst_exit_mode"] = min(returns, key=returns.get)
        else:
            comp["best_exit_mode"] = None
            comp["worst_exit_mode"] = None

        # fast vs current diff
        if "fast_exit" in returns and "current_exit" in returns:
            comp["fast_minus_current_return"] = round(returns["fast_exit"] - returns["current_exit"], 6)
        else:
            comp["fast_minus_current_return"] = None

        rows.append(comp)

    return pl.DataFrame(rows)


def save_paired_replay(
    per_mode_trades: dict[str, pl.DataFrame],
    comparison: pl.DataFrame,
    run_dir: Path,
) -> None:
    """保存 paired replay 结果。"""
    for mode_name, trades_df in per_mode_trades.items():
        mode_dir = run_dir / f"exit_mode={mode_name}"
        mode_dir.mkdir(parents=True, exist_ok=True)
        trades_df.write_parquet(str(mode_dir / "trades.parquet"))

    comparison.write_parquet(str(run_dir / "paired_exit_comparison.parquet"))
    logger.info(f"Paired replay saved to {run_dir}")
