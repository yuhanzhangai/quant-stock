"""Tests for paper_runner.py — 8h rolling monitor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestPersistentState:
    """Persistent state load/save tests."""

    def test_load_new_state(self, tmp_path):
        """First load creates fresh state with observation_id."""
        import scripts.paper_runner as runner

        orig = runner.STATE_PATH
        runner.STATE_PATH = tmp_path / "state.json"
        try:
            state = runner.load_state()
            assert "observation_id" in state
            assert state["observation_id"].startswith("obs_")
            assert state["cycle_number"] == 0
            assert state["positions"] == {}
            assert state["total_signals"] == 0
        finally:
            runner.STATE_PATH = orig

    def test_save_and_reload(self, tmp_path):
        """State survives save + reload."""
        import scripts.paper_runner as runner

        orig = runner.STATE_PATH
        runner.STATE_PATH = tmp_path / "state.json"
        try:
            state = runner.load_state()
            state["positions"]["ETH-USDT"] = {"entry_price": 3000, "entry_ts": "2026-01-01"}
            state["cycle_number"] = 3
            state["total_signals"] = 42
            runner.save_state(state)

            reloaded = runner.load_state()
            assert reloaded["positions"]["ETH-USDT"]["entry_price"] == 3000
            assert reloaded["cycle_number"] == 3
            assert reloaded["total_signals"] == 42
        finally:
            runner.STATE_PATH = orig

    def test_positions_persist_across_saves(self, tmp_path):
        """Open positions are not lost on save."""
        import scripts.paper_runner as runner

        orig = runner.STATE_PATH
        runner.STATE_PATH = tmp_path / "state.json"
        try:
            state = runner.load_state()
            state["positions"]["SOL-USDT"] = {"entry_price": 80, "entry_ts": "2026-04-24"}
            runner.save_state(state)

            state2 = runner.load_state()
            assert "SOL-USDT" in state2["positions"]
            assert state2["positions"]["SOL-USDT"]["entry_price"] == 80
        finally:
            runner.STATE_PATH = orig


class TestCycleDB:
    """Cycle DB initialization tests."""

    def test_init_creates_all_tables(self, tmp_path):
        """Cycle DB has all required tables."""
        import scripts.paper_runner as runner

        cycle_dir = tmp_path / "cycle_001"
        cycle_dir.mkdir()
        conn = runner.init_cycle_db(cycle_dir)

        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {t[0] for t in tables}
        required = {"signals", "decisions", "fills", "trades", "rejected_signals", "heartbeats"}
        missing = required - table_names
        assert not missing, f"Missing tables: {missing}"
        conn.close()

    def test_signal_insert(self, tmp_path):
        """Can insert signal into cycle DB."""
        import scripts.paper_runner as runner

        cycle_dir = tmp_path / "cycle_002"
        cycle_dir.mkdir()
        conn = runner.init_cycle_db(cycle_dir)
        conn.execute(
            "INSERT INTO signals (ts, session, symbol, side, price, status, reject_reason) "
            "VALUES ('2026-04-24', 'core', 'ETH-USDT', 'long', 3000.0, 'accepted', '')"
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert count == 1
        conn.close()


class TestSessionConfig:
    """Session configuration tests."""

    def test_total_symbols_47(self):
        """All 3 sessions total 47 symbols."""
        import scripts.paper_runner as runner

        total = sum(len(s["symbols"]) for s in runner.SESSIONS.values())
        assert total == 47

    def test_no_symbol_overlap(self):
        """No symbol appears in multiple sessions."""
        import scripts.paper_runner as runner

        all_syms = []
        for s in runner.SESSIONS.values():
            all_syms.extend(s["symbols"])
        assert len(all_syms) == len(set(all_syms)), "Duplicate symbols across sessions"

    def test_core_has_production_coins(self):
        """Core session contains the 4 production coins."""
        import scripts.paper_runner as runner

        core = set(runner.SESSIONS["core"]["symbols"])
        assert core == {"ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"}

    def test_broad_is_not_full_tracking(self):
        """Broad session does not have full position tracking."""
        import scripts.paper_runner as runner

        assert runner.SESSIONS["broad"]["full_tracking"] is False

    def test_core_candidate_are_full_tracking(self):
        """Core and candidate have full position tracking."""
        import scripts.paper_runner as runner

        assert runner.SESSIONS["core"]["full_tracking"] is True
        assert runner.SESSIONS["candidate"]["full_tracking"] is True
