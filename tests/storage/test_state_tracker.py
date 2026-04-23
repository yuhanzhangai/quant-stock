"""StateTracker 测试。"""

from pathlib import Path

import pytest

from src.storage.state_tracker import StateTracker


@pytest.fixture
def tracker(tmp_path: Path) -> StateTracker:
    db_path = tmp_path / "test_meta.sqlite"
    return StateTracker(db_path)


class TestStateTracker:
    def test_get_nonexistent(self, tracker: StateTracker) -> None:
        """不存在的记录应返回 None。"""
        result = tracker.get_last_timestamp("ohlcv", "BTC-USDT", "1h")
        assert result is None

    def test_update_and_get(self, tracker: StateTracker) -> None:
        """更新后应能读取。"""
        tracker.update_last_timestamp("ohlcv", "BTC-USDT", "1h", 1704067200000)
        result = tracker.get_last_timestamp("ohlcv", "BTC-USDT", "1h")
        assert result == 1704067200000

    def test_update_overwrite(self, tracker: StateTracker) -> None:
        """重复更新应覆盖旧值。"""
        tracker.update_last_timestamp("ohlcv", "BTC-USDT", "1h", 1704067200000)
        tracker.update_last_timestamp("ohlcv", "BTC-USDT", "1h", 1704070800000)
        result = tracker.get_last_timestamp("ohlcv", "BTC-USDT", "1h")
        assert result == 1704070800000

    def test_different_sources(self, tracker: StateTracker) -> None:
        """不同 source 应互不影响。"""
        tracker.update_last_timestamp("ohlcv", "BTC-USDT", "1h", 100)
        tracker.update_last_timestamp("funding", "BTC-USDT", "1h", 200)
        assert tracker.get_last_timestamp("ohlcv", "BTC-USDT", "1h") == 100
        assert tracker.get_last_timestamp("funding", "BTC-USDT", "1h") == 200

    def test_universe_crud(self, tracker: StateTracker) -> None:
        """标的池增删改查。"""
        tracker.update_universe("BTC-USDT", "spot", "USDT", 1e9, 1)
        tracker.update_universe("ETH-USDT", "spot", "USDT", 5e8, 2)

        universe = tracker.get_universe("spot", top_n=10)
        assert len(universe) == 2
        assert universe[0]["symbol"] == "BTC-USDT"
        assert universe[0]["rank"] == 1

    def test_universe_update(self, tracker: StateTracker) -> None:
        """标的池更新应覆盖旧值。"""
        tracker.update_universe("BTC-USDT", "spot", "USDT", 1e9, 1)
        tracker.update_universe("BTC-USDT", "spot", "USDT", 2e9, 1)

        universe = tracker.get_universe("spot")
        assert len(universe) == 1
        assert universe[0]["volume_24h"] == 2e9
