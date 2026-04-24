# v2.3 Paper Observation Final Report

> Date: 2026-04-24
> Covers: v2.3 C0-C7, v2.3 Integrity Gate, v2.3R Replay, v2.3R.1 Compliance Fix

## Summary

v2.3 achieved all analytical goals. Paper calibration supports backtest assumptions.
FastExit confirmed as exit variant (96% overlap), demoted to research.
HybridExit discovered as promising research lead, forwarded to v2.4.

## Completed Work

| Phase | Status | Key Finding |
|-------|--------|-------------|
| C0-C6 | DONE | Observation config, sessions, overlap analysis, RiskEngine audit |
| C7 live observation | Day 1 baseline established | Ongoing (needs 14d for full completion) |
| Integrity Gate | DONE | backtest_runs=0 fixed, random_baseline=ERROR fixed, 9/9 gate usable |
| v2.3R Phases 1-9 | DONE | 4 exit_modes compared, FastExit +0.009%/trade (noise) |
| v2.3R.1 Compliance | DONE | Artifacts persisted, DB written, tests added, gate objects aligned |

## Strategy Conclusions

### MinSwing v3
**remain_production**
- Paper calibration: ETH +24.5%, SOL +9.4%
- Signal count matches backtest (93 vs 92)
- Reproducibility: 3/3 runs identical
- Cost ~42% of gross edge, survivable
- RiskEngine: 6.6% reject rate, 0 false kills

### FastExit ETH
**remain_research_exit_mode** (downgraded from candidate)
- 96% entry overlap with MinSwing v3 → exit_variant_only
- v2.3R result: +0.009%/trade over current (noise level)
- Fails 4/7 promotion criteria (windowed stability, PF, drawdown, fee ratio)
- 60-day window: -0.047%/trade (underperforms)
- Wins only 17% of individual entry comparisons
- Not eligible for v2.4 integration

### HybridExit
**promising_research_lead** → forwarded to v2.4
- Unexpected finding from v2.3R
- Portfolio return: +31.2% vs current +26.5%
- Profit factor: 2.53 vs 2.37
- Max drawdown: -5.2% vs -5.8%
- Fee/gross: 32.1% vs 34.7%
- Requires independent preregistered validation (v2.4)

### Short Strategies
**candidate_blocked** (unchanged)
- Validation pipeline does not support short-side workflows

## v2.3 C7 Live Observation Status

Day 1 baseline established. Full 14-day window not yet reached.
API verified working. Data quality 7/7 PASS on ETH/SOL.
Live observation will continue in background during v2.4.

## Next Steps

1. v2.4: Hybrid Exit Candidate Validation (preregistered experiment)
2. v2.5: Shadow Paper (conditional on v2.4 = promote_to_shadow_paper)
3. v2.6: Promotion Review
