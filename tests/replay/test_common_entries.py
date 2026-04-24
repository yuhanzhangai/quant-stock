"""Tests for common entry generator."""

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
def has_data():
    """Skip if no ETH-USDT 5m data."""
    from config.settings import get_settings
    from src.storage.parquet_writer import ParquetWriter

    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv("ETH-USDT", "5m")
    if df.is_empty():
        pytest.skip("No ETH-USDT 5m data")


class TestCommonEntries:
    """Common entries generator tests."""

    def test_deterministic(self, config_exists, has_data):
        """Same data + same config = same entry_id across two runs."""
        from src.replay.common_entries import generate_common_entries

        run1 = generate_common_entries(CONFIG_PATH)
        run2 = generate_common_entries(CONFIG_PATH)

        assert len(run1) == len(run2), "Entry count differs between runs"
        assert run1["entry_id"].to_list() == run2["entry_id"].to_list(), "entry_id differs between runs"
        assert run1["entry_ts"].to_list() == run2["entry_ts"].to_list(), "entry_ts differs between runs"

    def test_required_columns(self, config_exists, has_data):
        """Output has all required columns."""
        from src.replay.common_entries import generate_common_entries

        df = generate_common_entries(CONFIG_PATH)

        required = {
            "entry_id",
            "symbol",
            "timeframe",
            "entry_ts",
            "entry_price",
            "side",
            "entry_reason",
            "entry_mode",
            "source_strategy",
            "source_strategy_version",
            "params_hash",
            "data_version",
            "created_at",
        }
        actual = set(df.columns)
        missing = required - actual
        assert not missing, f"Missing columns: {missing}"

    def test_no_exit_fields(self, config_exists, has_data):
        """Entries must not contain exit-related fields."""
        from src.replay.common_entries import generate_common_entries

        df = generate_common_entries(CONFIG_PATH)

        exit_fields = {"exit_ts", "exit_price", "exit_reason", "exit_mode", "pnl", "return_pct"}
        present = exit_fields & set(df.columns)
        assert not present, f"Exit fields found in entries: {present}"

    def test_entries_not_empty(self, config_exists, has_data):
        """Must generate at least some entries."""
        from src.replay.common_entries import generate_common_entries

        df = generate_common_entries(CONFIG_PATH)
        assert len(df) > 0, "No entries generated"

    def test_all_entries_are_long(self, config_exists, has_data):
        """All entries must be long side."""
        from src.replay.common_entries import generate_common_entries

        df = generate_common_entries(CONFIG_PATH)
        sides = df["side"].unique().to_list()
        assert sides == ["long"], f"Expected only 'long', got {sides}"
