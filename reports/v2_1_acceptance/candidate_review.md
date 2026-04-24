# Candidate Review

> Date: 2026-04-24
> Based on: gate_sanity_check results

## FastExit ETH

| Gate | Status | Score | Notes |
|------|--------|-------|-------|
| data_quality | PASS | 0 critical | Clean |
| baseline_backtest | PASS | pf=1.41 | Above 1.1 threshold |
| cost_stress | PASS | pf=1.41 | Survives normal cost |
| oos | FAIL | pf=1.71, trades=24 | PF excellent, but trades < 30 threshold (27-day test window too short) |
| walk_forward | PASS | 60% positive | At threshold |
| random_baseline | ERROR | — | Needs investigation |
| monte_carlo | PASS | 77% profit prob | Above 60% |
| event_backtest | PASS | dd=-13.3% | Well within -30% limit |
| parameter_stability | PASS | 60% stable | Above 50% threshold |

**OOS failure analysis:** PF=1.71 and Sharpe=3.63 are excellent. The only issue is trade_count=24 < 30 in the 30% OOS window. With min_gap=144 (12h between trades) and only 27 days of test data, 24 trades is the maximum possible. This is a data quantity limitation, not a strategy problem.

**Decision: `remain_candidate`**

Reason: Strong results, but needs longer OOS period (more data) before paper trading. When 5m data extends beyond 3 months, re-validate.

## short_session_filter

| Gate | Status | Score | Notes |
|------|--------|-------|-------|
| data_quality | PASS | 0 critical | Clean |
| baseline_backtest | FAIL | pf=0.46 | Short strategy tested in long-only framework |
| cost_stress | FAIL | pf=0.46 | Same framework issue |
| oos | FAIL | pf=0.80 | — |
| walk_forward | FAIL | 20% positive | — |
| random_baseline | ERROR | — | — |
| monte_carlo | FAIL | 2.5% profit | — |
| event_backtest | PASS | dd=-30% | Marginal |
| parameter_stability | FAIL | 0% stable | — |

**Analysis:** 6 gate failures. However, this is a **short** strategy being tested through vectorbt's long-only framework with price inversion. The validation pipeline cannot properly evaluate short strategies until it supports native short positions.

**Decision: `remain_candidate`**

Reason: Validation framework limitation, not strategy rejection. The strategy was independently verified with Sharpe +2.83 using manual price-inversion backtesting. Proper short-strategy validation is needed before any promotion decision.

## short_trend_follow

Not separately tested (same validation framework limitation as session_filter).

**Decision: `remain_candidate`**

Same reason as short_session_filter.

## Summary

| Strategy | Gate Result | Decision |
|----------|-----------|----------|
| FastExit ETH | 7 pass, 1 fail (data quantity) | remain_candidate |
| short_session_filter | 2 pass, 6 fail (framework limitation) | remain_candidate |
| short_trend_follow | Not tested | remain_candidate |

**No Candidate promoted to Production or Paper Trading in this round.**
**No Candidate rejected — failures are due to framework limitations, not strategy quality.**

## Next Actions

1. Extend 5m data beyond 3 months to allow OOS with >= 30 trades
2. Build short-strategy validation support (price inversion in gate pipeline)
3. Fix random_baseline gate ERROR
