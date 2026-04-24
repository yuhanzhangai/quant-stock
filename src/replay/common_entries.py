"""Common entry generator — 生成确定性 MinSwing 入场信号。

所有 exit_mode replay 共用同一批 entry。
entry_id 是确定性 hash：同一数据 + 同一配置 = 同一 entry_id。
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import polars as pl
import yaml
from loguru import logger

from src.research.db import connect_research_db


def generate_common_entries(config_path: Path) -> pl.DataFrame:
    """从 replay config 生成 common entry set。

    Args:
        config_path: config/replay/v2_3_exit_mode_replay.yml

    Returns:
        Polars DataFrame with deterministic entry_id per entry.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    symbol = config["data"]["symbols"][0]
    timeframe = config["data"]["timeframe"]
    data_version = config["data"]["data_version"]
    strategy_name = config["entry"]["source_strategy"]
    strategy_version = config["entry"]["source_strategy_version"]

    # Load price data
    from config.settings import get_settings
    from src.storage.parquet_writer import ParquetWriter

    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, timeframe)

    if df.is_empty():
        raise ValueError(f"No data for {symbol}/{timeframe}")

    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    pdf = pdf.set_index("datetime").sort_index()
    price = pdf["close"]

    # Generate entries using MinSwing v3 entry logic ONLY
    from src.strategies.minswing_v3_final import MinSwingV3Strategy

    strat = MinSwingV3Strategy()
    coin = symbol.replace("-USDT", "")
    entries, _ = strat.generate_signals(price, coin=coin)

    # Build params_hash for deterministic entry_id
    params = {
        "trend_ma": 180,
        "stop_pct": 2.0,
        "take_profit_pct": 8.0,
        "min_gap": 144,
    }
    params_hash = hashlib.md5(str(sorted(params.items())).encode()).hexdigest()[:10]

    # Extract entry bars
    entry_rows = []
    for i in range(len(price)):
        if entries.iloc[i]:
            entry_ts = str(price.index[i])
            entry_price = float(price.iloc[i])

            # Deterministic entry_id
            raw = f"{symbol}|{timeframe}|{entry_ts}|long|{params_hash}"
            entry_id = hashlib.sha256(raw.encode()).hexdigest()[:16]

            entry_rows.append(
                {
                    "entry_id": entry_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "entry_ts": entry_ts,
                    "entry_price": entry_price,
                    "entry_bar_idx": i,
                    "side": "long",
                    "entry_reason": "trend_ma+rsi+macd",
                    "entry_mode": "standard",
                    "source_strategy": strategy_name,
                    "source_strategy_version": strategy_version,
                    "params_hash": params_hash,
                    "data_version": data_version,
                    "created_at": datetime.now(tz=UTC).isoformat(),
                }
            )

    result = pl.DataFrame(entry_rows)
    logger.info(f"Generated {len(result)} common entries for {symbol}/{timeframe}")
    return result


def save_common_entries(
    entries_df: pl.DataFrame,
    run_id: str,
    config_path: Path,
) -> Path:
    """保存 common entries 到 parquet + 注册到 DB。

    Args:
        entries_df: common entries DataFrame
        run_id: replay run identifier
        config_path: config file path for reference

    Returns:
        Path to saved parquet file.
    """
    output_dir = Path("data/research/replay") / f"run_id={run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / "common_entries.parquet"
    entries_df.write_parquet(str(path))
    logger.info(f"Common entries saved: {path} ({len(entries_df)} entries)")

    # Register in experiment_runs
    conn = connect_research_db(required=True)
    conn.execute(
        """
        INSERT INTO experiment_runs
        (run_id, experiment_name, strategy_name, status, config_path,
         code_commit, data_version, created_at)
        VALUES (?, 'minswing_exit_mode_replay', 'minswing_v3', 'running',
                ?, ?, ?, current_timestamp)
        """,
        [
            run_id,
            str(config_path),
            _get_git_commit(),
            entries_df["data_version"][0] if len(entries_df) > 0 else "",
        ],
    )
    conn.close()
    logger.info(f"Experiment registered in DB: {run_id}")

    return path


def _get_git_commit() -> str:
    """获取当前 git commit hash。"""
    import subprocess

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
