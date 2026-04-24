# Changelog

## v2.1-validation-hardening (2026-04-24)

Core validation goals achieved, paper calibration deferred to v2.2.

### Completed
- Data quality: 47/47 warnings reviewed, 0 unexplained
- Reproducibility: MinSwing v3 produces identical results across 3 runs
- Gate sanity: overfit strategies correctly rejected (Ichimoku/MACD_Hist 6/9 fail)
- Candidate review: FastExit ETH remain_candidate, short strategies blocked by framework

### Deferred
- Paper calibration → v2.2-paper-calibration

## v2.0.1-persistence-hardening (2026-04-24)

research.duckdb is now the single source of truth.

- src/research/db.py: centralized DB connection with fail-fast
- All silent `if not DB_PATH.exists(): return` eliminated
- save_all() no longer has write_db escape hatch
- PaperSession supports context manager with auto-finalize
- Dashboard pages 4/10 read from DB, not glob directories
- backtest_runs: added run_type/parent_run_id/output_dir columns
- Grid search writes all candidates + best to DB

## v2.0 (2026-04-23)

13-checkpoint research infrastructure overhaul (C0-C12).

- C0: Baseline frozen (tag v1.1-research-baseline)
- C1: Strategy registry + frozen configs
- C2: research.duckdb (7 tables)
- C3: Data manifest (90 files, 668K rows)
- C4: Data quality gate (7 checks)
- C5: Standardized backtest output
- C6: Experiment ledger
- C7: 9-gate validation pipeline
- C8: Cost/slippage/risk models
- C9: Strategy gate policy
- C10: Paper session management
- C11: Dashboard upgrade (5 new research pages)
- C12: Daily workflow checklists

## v1.1.0-final (2026-04-22)

Strategy research complete. 74+ rounds of iteration.

- MinSwing v3: production champion (Sharpe +2.13)
- FastExit ETH: +34% improvement combo
- 40+ strategies tested, 3 confirmed overfit
- Monte Carlo: 72% profit probability
- Dashboard: 5 pages with K-line chart + backtest
