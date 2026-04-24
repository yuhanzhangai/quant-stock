"""Tests for entry-level paired replay."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

CONFIG_PATH = Path("config/replay/v2_3_exit_mode_replay.yml")


@pytest.fixture
def config_exists():
    """Skip if config doesn't exist."""
    if not CONFIG_PATH.exists():
        pytest.skip("Replay config not found")


@pytest.fixture
def replay_data(config_exists):
    """Generate entries + run paired replay."""
    import pandas as pd

    from config.settings import get_settings
    from src.replay.common_entries import generate_common_entries
    from src.replay.paired_replay import run_paired_replay
    from src.storage.parquet_writer import ParquetWriter

    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv("ETH-USDT", "5m")
    if df.is_empty():
        pytest.skip("No ETH-USDT 5m data")

    entries = generate_common_entries(CONFIG_PATH)
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    price = pdf.set_index("datetime").sort_index()["close"]

    per_mode, comparison = run_paired_replay(entries, price)
    return entries, per_mode, comparison


class TestPairedReplay:
    """Paired replay tests."""

    def test_all_exit_modes_use_same_entries(self, replay_data):
        """All exit modes must use the exact same entry_id set."""
        entries, per_mode, _ = replay_data
        entry_ids = set(entries["entry_id"].to_list())

        for mode_name, trades in per_mode.items():
            trade_entry_ids = set(trades["entry_id"].to_list())
            assert trade_entry_ids == entry_ids, f"{mode_name} has different entry_ids"

    def test_paired_comparison_has_one_row_per_entry(self, replay_data):
        """Comparison table must have exactly one row per entry."""
        entries, _, comparison = replay_data
        assert len(comparison) == len(entries), f"Comparison has {len(comparison)} rows, entries has {len(entries)}"

    def test_fast_exit_not_independent_entry_logic(self, replay_data):
        """fast_exit must use the same entries as current_exit (not generate its own)."""
        _, per_mode, _ = replay_data
        current_ids = per_mode["current_exit"]["entry_id"].to_list()
        fast_ids = per_mode["fast_exit"]["entry_id"].to_list()
        assert current_ids == fast_ids, "fast_exit generated independent entries"

    def test_exit_mode_outputs_required_columns(self, replay_data):
        """Each exit_mode trade output must have required columns."""
        _, per_mode, _ = replay_data
        required = {
            "entry_id",
            "symbol",
            "entry_ts",
            "entry_price",
            "exit_price",
            "exit_mode",
            "exit_reason",
            "return_pct",
            "mae_pct",
            "mfe_pct",
            "holding_bars",
        }

        for mode_name, trades in per_mode.items():
            actual = set(trades.columns)
            missing = required - actual
            assert not missing, f"{mode_name} missing columns: {missing}"
