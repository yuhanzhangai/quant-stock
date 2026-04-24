"""Tests for top50_paper_monitor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestSessionDB:
    """Session DB initialization tests."""

    def test_init_creates_tables(self, tmp_path):
        """init_session_db creates signals and heartbeats tables."""
        import scripts.top50_paper_monitor as monitor

        original = monitor.SESSION_DB
        monitor.SESSION_DB = tmp_path / "test_session.sqlite"
        try:
            conn = monitor.init_session_db()
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = {t[0] for t in tables}
            assert "signals" in table_names
            assert "heartbeats" in table_names
            conn.close()
        finally:
            monitor.SESSION_DB = original

    def test_signal_insert(self, tmp_path):
        """Can insert a signal record."""
        import scripts.top50_paper_monitor as monitor

        original = monitor.SESSION_DB
        monitor.SESSION_DB = tmp_path / "test_session.sqlite"
        try:
            conn = monitor.init_session_db()
            conn.execute(
                "INSERT INTO signals (ts, symbol, side, price, confidence, status, reject_reason) "
                "VALUES ('2026-01-01', 'ETH-USDT', 'long', 3000.0, 'MED', 'accepted', '')"
            )
            conn.commit()
            count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            assert count == 1
            conn.close()
        finally:
            monitor.SESSION_DB = original


class TestHeartbeat:
    """Heartbeat generation tests."""

    def test_heartbeat_has_all_fields(self):
        """Heartbeat must contain all 13 required fields."""
        import scripts.top50_paper_monitor as monitor

        hb = monitor.generate_heartbeat(
            api_ok=True,
            max_delay=0.5,
            symbols_updated=47,
            db_ok=True,
            scan_stats={"signals": 2, "accepted": 1, "rejected": 1},
            error_count=0,
        )
        required = {
            "ts",
            "status",
            "api_health",
            "latest_bar_delay_max",
            "symbols_updated",
            "db_write_ok",
            "active_positions",
            "signal_count",
            "accepted_count",
            "rejected_count",
            "error_count",
            "disk_free_gb",
            "memory_mb",
        }
        assert required.issubset(set(hb.keys())), f"Missing: {required - set(hb.keys())}"

    def test_heartbeat_failed_on_api_down(self):
        """API failure → heartbeat status = failed."""
        import scripts.top50_paper_monitor as monitor

        hb = monitor.generate_heartbeat(
            api_ok=False,
            max_delay=0.5,
            symbols_updated=0,
            db_ok=True,
            scan_stats={},
            error_count=0,
        )
        assert hb["status"] == "failed"

    def test_heartbeat_warning_on_high_delay(self):
        """High bar delay → heartbeat status = warning."""
        import scripts.top50_paper_monitor as monitor

        hb = monitor.generate_heartbeat(
            api_ok=True,
            max_delay=5.0,
            symbols_updated=47,
            db_ok=True,
            scan_stats={},
            error_count=0,
        )
        assert hb["status"] == "warning"

    def test_heartbeat_normal(self):
        """Normal conditions → heartbeat status = normal."""
        import scripts.top50_paper_monitor as monitor

        hb = monitor.generate_heartbeat(
            api_ok=True,
            max_delay=0.5,
            symbols_updated=47,
            db_ok=True,
            scan_stats={},
            error_count=0,
        )
        assert hb["status"] == "normal"


class TestUniverse:
    """Universe loading tests."""

    def test_load_universe_returns_list(self):
        """load_universe returns a list of symbol strings."""
        import scripts.top50_paper_monitor as monitor

        symbols = monitor.load_universe()
        assert isinstance(symbols, list)
        assert len(symbols) > 0
        assert all(s.endswith("-USDT") for s in symbols)

    def test_load_universe_excludes_removed(self):
        """BASED-USDT and ROBO-USDT should not be in universe."""
        import scripts.top50_paper_monitor as monitor

        symbols = monitor.load_universe()
        assert "BASED-USDT" not in symbols
        assert "ROBO-USDT" not in symbols
