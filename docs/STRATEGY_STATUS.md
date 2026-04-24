# Strategy Status

> Last updated: 2026-04-23

## Quick Summary

**Production (1):** MinSwing v3
**Candidate (3):** FastExit ETH, short_session_filter, short_trend_follow
**Research (4):** Dual, ExtremeReversal, AggressiveMom 4h, IchimokuMom 4h
**Archive:** 40+ long, 4 short, 2 combo, 6 meta, 1 TSLA

## Production

| Strategy | File | Direction | Timeframe | Symbols | Sharpe |
|----------|------|-----------|-----------|---------|--------|
| MinSwing v3 | src/strategies/minswing_v3_final.py | long | 5m | ETH, SOL, NEAR, ARB | +2.13 |

Config: `config/strategies/minswing_v3.yml`
Rules: Only bug fixes. No silent parameter changes. No unrecorded optimization.

## Candidate

| Strategy | File | Direction | Timeframe | Symbols | Sharpe |
|----------|------|-----------|-----------|---------|--------|
| FastExit ETH | src/strategies/combo/fast_exit.py | long | 5m | ETH | +3.753 |
| session_filter | src/strategies/short/short_session_filter.py | short | 5m | ARB, NEAR, FIL, DOT, PEPE, OP, ETH | +2.83 |
| trend_follow | src/strategies/short/short_trend_follow.py | short | 5m | SOL, SUI, ATOM | +2.35 |

Config: `config/strategies/fast_exit_eth.yml`, `config/strategies/short_session_filter.yml`
Rules: Needs full validation pipeline before promotion to production.

## Research

| Strategy | Direction | Timeframe | Reason |
|----------|-----------|-----------|--------|
| MinSwing Dual | long | 5m | Too similar to v3 for simultaneous production |
| ExtremeReversal | long | 5m | Low trade count, needs more sample |
| AggressiveMom | long | 4h | Best 4h (OOS +0.572), separate research track |
| IchimokuMom | long | 4h | OOS +0.443, lower priority |

## Archive

| Group | Count | Reason |
|-------|-------|--------|
| long/ strategies | 41 | Historical iteration archive |
| short archive | 4 | Weaker short strategies |
| combo archive | 2 | fund_mode failed, long_short_auto signals don't overlap |
| meta strategies | 6 | Not validated through pipeline |
| TSLA experiment | 1 | Cross-market, separate track |

## Confirmed Overfit (DO NOT REACTIVATE)

| Strategy | In-Sample | OOS | Verdict |
|----------|-----------|-----|---------|
| Ichimoku standalone | +0.89 | -0.37 | FRAUD |
| MACD_Hist | +0.69 | -0.69 | FRAUD |
| MomBreakout | +1.15 | -0.68 | FRAUD |

## Paused Scripts

These scripts are paused until validation pipeline (Checkpoint 7) is complete:

- `scripts/tournament.py` — Strategy tournament
- `scripts/tournament_live.py` — 32-strategy paper trading
- `scripts/short_iterate.py` — Short strategy iteration
- `scripts/run_all_strategies.py` — Batch strategy backtest
