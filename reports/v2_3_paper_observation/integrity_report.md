# v2.3 Integrity Report

> Date: 2026-04-24

## Issue 1: backtest_runs = 0

**Root cause:** v2.0.1 added new columns (run_type, parent_run_id, output_dir, etc.) to `init_research_db.py` schema definition, but the existing `research.duckdb` file was never rebuilt. The old DB lacked these columns, so `save_to_db()` with new fields would fail silently in some cases, or the columns simply didn't exist for insertion.

**Fix:** ALTER TABLE to add 7 missing columns to the live database.

**Verification:** Ran MinSwing v3 backtest → backtest_runs now has 1 row (sharpe=3.17, trades=82, run_type=single).

**Status: RESOLVED**

## Issue 2: random_baseline = ERROR

**Root cause:** In `gate_random_baseline()`, the random exit signal was generated via:
```python
rand_exits = rand_entries.shift(20).fillna(False)
```
`fillna(False)` on a shifted boolean Series produces `object` dtype, not `bool`. vectorbt's numba JIT compilation rejects `array(pyobject, 1d, C)` as a non-numeric type, causing `nopython mode` failure.

**Fix:** Added explicit dtype:
```python
rand_entries = pd.Series(False, index=price.index, dtype=bool)
rand_exits = rand_entries.shift(20).fillna(False).astype(bool)
```

**Verification:** MinSwing v3 random_baseline now returns PASS (strategy +13.3% vs random P75 -19.0%).

**Status: RESOLVED**

## Impact Assessment

- backtest_runs fix: all future backtests will correctly persist to DB. Historical backtests (v2.2 paper calibration) were done via PaperSession, not through standardized_output, so they are recorded in paper_sessions table (not backtest_runs). No data loss.
- random_baseline fix: the 9-gate validation pipeline is now a complete 9/9 gate system instead of 8/9 + 1 ERROR. Gate sanity check conclusions remain valid — the ERROR gate didn't affect pass/fail decisions for other gates.

## Both issues are now resolved. 9-gate validation is fully operational.
