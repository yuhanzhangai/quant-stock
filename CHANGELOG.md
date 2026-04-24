# Changelog

## v2.3-paper-observation-hardening (2026-04-24)

Paper observation + historical replay maturity.

### Key Findings
- FastExit ETH: 96% entry overlap with MinSwing → exit_variant_only
- FastExit: +0.009%/trade over current (noise), 4/7 criteria failed → demoted to research
- HybridExit (unexpected): +31.2% vs current +26.5%, PF 2.53, best exit_mode → forwarded to v2.4
- 9-gate: random_baseline fixed, all gates 0 ERROR

### Infrastructure
- v2.3R.1 compliance fix: portfolio artifacts persisted, backtest_runs written,
  windowed/cost summaries saved, 9-gate validation objects aligned
- New modules: src/replay/ (common_entries, exit_modes, paired_replay, portfolio_replay)
- Tests: 49/49 pass (9 new replay tests)

### Strategy Status Changes
- FastExit ETH: candidate → research (remain_research_exit_mode)
- HybridExit: new → research_lead (promising, requires v2.4 validation)
- MinSwing v3: remain_production
- Short strategies: remain candidate_blocked

## v2.2-paper-calibration (2026-04-24)

Paper trading calibration against live OKX data.

- API preflight: all 4 checks PASS (public, private, K-line, freshness)
- Data refreshed: ETH/SOL/NEAR/ARB +1099 new 5m candles
- Data quality: 7/7 PASS on refreshed ETH/SOL
- MinSwing v3 paper session: ETH $62.23 (+24.5%), SOL $54.71 (+9.4%)
- FastExit ETH paper session: $63.57 (+27.1%)
- RiskEngine: 18 rejections across 3 sessions (all cooldown_after_losses), 0 false kills
- Signal count matches backtest (93 paper vs 92 backtest)
- Cost ~42% of gross edge — significant but survivable
- MinSwing v3: confirmed Production
- FastExit ETH: remain_candidate

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
