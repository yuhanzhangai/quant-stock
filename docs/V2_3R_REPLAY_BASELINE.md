# v2.3R Replay Baseline

> Frozen at: 2026-04-24
> This document records the state before v2.3R historical replay begins.
> No strategy code or parameters may be changed during v2.3R.

## Commit & Data

| Item | Value |
|------|-------|
| Git commit | e3c5352 |
| Branch | v2.3-paper-observation-hardening |
| Data version | manifest_20260424_031446 |
| Data files | 90 parquet, 669K rows |
| Data quality | 336 checks (all pass/warning, 0 critical) |

## Integrity Gate Status

| Issue | Status |
|-------|--------|
| backtest_runs=0 | FIXED — ALTER TABLE added 7 columns, test write successful |
| random_baseline=ERROR | FIXED — dtype=bool fix, now returns PASS |
| 9-gate validation | 9/9 operational, 0 ERROR |

## Strategy Status

| Strategy | Status | v2.2 Decision |
|----------|--------|---------------|
| MinSwing v3 | Production | remain_production |
| FastExit ETH | Candidate | remain_candidate |
| short_session_filter | Candidate (blocked) | blocked (no short pipeline) |
| short_trend_follow | Candidate (blocked) | blocked (no short pipeline) |

## Critical Finding

**FastExit ETH and MinSwing v3 have 96% entry overlap.**

FastExit is NOT an independent strategy. It is an exit variant of MinSwing v3.
The +27.1% vs +24.5% performance difference comes entirely from exit timing.

## v2.3R Purpose

Test whether FastExit's exit logic should be treated as a MinSwing exit_mode candidate.

**Question:** Same MinSwing entries, different exit_mode — which is more stable?

## Rules

- No entry logic changes
- No parameter optimization
- No strategy promotion
- All results must write to research.duckdb
- All replay uses the same common entry set

## research.duckdb State

| Table | Rows |
|-------|------|
| backtest_runs | 1 |
| data_manifest | 90 |
| data_quality_checks | 336 |
| experiment_runs | 1 |
| paper_sessions | 6 |
| strategy_registry | 0 |
| validation_results | 45 |
