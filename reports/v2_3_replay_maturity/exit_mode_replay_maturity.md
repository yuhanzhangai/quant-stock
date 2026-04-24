# v2.3R Historical Replay Maturity Report

> Date: 2026-04-24
> Symbol: ETH-USDT 5m
> Baseline commit: e3c5352
> Data version: manifest_20260424_031446

## 1. Purpose

FastExit ETH has 96% entry overlap with MinSwing v3.
It is evaluated as an exit_mode candidate, not an independent strategy.

## 2. Common Entry Set

- Entry count: 93
- Source: MinSwing v3 standard entry (trend_ma=180, RSI, MACD)
- Deterministic: 2 runs produce identical entry_id sets

## 3. Entry-Level Paired Replay

| Exit Mode | Avg Return/Trade | Avg Holding | vs Current |
|-----------|-----------------|-------------|------------|
| current_exit | +0.4051% | 74 bars | baseline |
| fast_exit | +0.4138% | 71 bars | +0.009% |
| trailing_exit | +0.4051% | 74 bars | 0% (identical for ETH) |
| hybrid_exit | +0.4536% | 67 bars | +0.049% |

Fast exit wins only 17% of individual trades vs current.

## 4. Portfolio-Constrained Replay

| Exit Mode | Trades | Final Equity | Return | PF | MaxDD | Fee/Gross |
|-----------|--------|-------------|--------|-----|-------|-----------|
| current_exit | 84 | $63.26 | +26.5% | 2.37 | -5.8% | 34.7% |
| fast_exit | 85 | $63.22 | +26.4% | 2.36 | -6.2% | 35.2% |
| trailing_exit | 84 | $63.26 | +26.5% | 2.37 | -5.8% | 34.7% |
| **hybrid_exit** | **85** | **$65.58** | **+31.2%** | **2.53** | **-5.2%** | **32.1%** |

Hybrid dominates on all metrics. Fast_exit is essentially flat vs current.

## 5. Windowed Replay

| Window | Entries | Current Avg | Fast Avg | Fast-Current | Fast Better? |
|--------|---------|-------------|----------|--------------|-------------|
| full_sample | 93 | +0.405% | +0.414% | +0.009% | Marginal |
| recent_90d | 92 | +0.408% | +0.417% | +0.009% | Marginal |
| recent_60d | 64 | +0.469% | +0.422% | **-0.047%** | **NO** |
| recent_30d | 31-32 | +0.37% | +0.46% | +0.09% | Yes |

**Fast_exit fails the windowed stability test:** it underperforms in the 60d window.
Only 2/4 windows show improvement — below the 3/4 threshold.

## 6. Cost Stress

| Exit Mode | Normal Return | Pessimistic Return | Survives |
|-----------|-------------|-------------------|----------|
| current_exit | +26.5% | +15.4% | YES |
| fast_exit | +26.4% | +15.2% | YES |
| trailing_exit | +26.5% | +15.4% | YES |
| hybrid_exit | +31.2% | +19.5% | YES |

All modes survive cost stress. No differentiation here.

## 7. 9-Gate Validation

| Gate | current_exit | fast_exit |
|------|-------------|-----------|
| data_quality | PASS | PASS |
| baseline_backtest | PASS (1.31) | PASS (1.39) |
| cost_stress | PASS (1.31) | PASS (1.39) |
| oos | FAIL (trades<30) | FAIL (trades<30) |
| walk_forward | PASS (0.60) | PASS (0.60) |
| random_baseline | PASS (+13.3%) | PASS (+16.2%) |
| monte_carlo | PASS (72%) | PASS (74%) |
| event_backtest | PASS (-11.8%) | PASS (-13.3%) |
| parameter_stability | FAIL (0.40) | PASS (0.60) |

Both have 0 ERROR gates. Fast_exit actually scores better on parameter_stability.

## 8. Decision

### Evidence Summary

| Criterion | fast_exit Result |
|-----------|-----------------|
| Multi-window advantage (≥3/4) | **FAIL** (2/4 windows) |
| Profit factor improvement | NO (+2.36 vs +2.37) |
| Max drawdown not worse | **WORSE** (-6.2% vs -5.8%) |
| Fee/gross not worse | **WORSE** (35.2% vs 34.7%) |
| Pessimistic cost survives | YES |
| 9-gate no ERROR | YES (0 errors) |
| Random baseline pass | YES |
| Entry-level advantage | MARGINAL (+0.009%/trade, 17% win rate) |

### Verdict

**Decision: `remain_research_exit_mode`**

### Reasoning

1. Fast_exit provides essentially zero edge over current_exit (+0.009%/trade, indistinguishable from noise)
2. Fast_exit **underperforms** in the 60-day window (-0.047%/trade)
3. Fast_exit slightly worsens drawdown (-6.2% vs -5.8%) and fee ratio
4. Only wins 17% of individual entry comparisons
5. Does not meet the ≥3/4 window stability threshold (2/4)

**However:** hybrid_exit shows genuine promise (+0.049%/trade, +31.2% portfolio return, best PF, lowest drawdown). This should be investigated further in v2.4 if exit_mode integration is pursued.

### Next Step

- FastExit as independent exit_mode: does not justify v2.4 integration
- Hybrid_exit: warrants further research as preregistered experiment in v2.4
- Current_exit remains the production default
- No parameter changes recommended

---

## 9. Compliance Fix Result (v2.3R.1)

| Fix | Status |
|-----|--------|
| A1: current_exit trail from COIN_CONFIG | DONE — no longer hardcoded |
| A2: Phase 4 automated tests | DONE — 4 tests added |
| A3: Portfolio replay artifacts persisted | DONE — 4 modes × 4 files |
| A3: backtest_runs written | DONE — 4 rows (exit_mode_portfolio_replay) |
| A4: Windowed summaries persisted | DONE — windowed_summary.csv/json |
| A5: Cost stress summaries persisted | DONE — cost_stress_summary.csv/json |
| A6: 9-gate validation object aligned | DONE — MinSwing entry + exit_mode, 0 ERROR |
| A7: Report updated | DONE — this section |

### 9-Gate Aligned Results

| Gate | current_exit | fast_exit | hybrid_exit |
|------|-------------|-----------|-------------|
| data_quality | PASS | PASS | PASS |
| baseline_backtest | PASS (1.31) | PASS (1.21) | PASS (1.44) |
| cost_stress | PASS (1.31) | **FAIL (1.21)** | PASS (1.44) |
| oos | FAIL (trades<30) | FAIL (trades<30) | FAIL (trades<30) |
| walk_forward | PASS (0.60) | PASS (0.60) | PASS (0.60) |
| random_baseline | PASS | PASS | PASS |
| monte_carlo | PASS (0.72) | PASS (0.63) | PASS (0.80) |
| event_backtest | PASS | PASS | PASS |
| parameter_stability | **FAIL (0.40)** | **PASS (0.60)** | **FAIL (0.40)** |

- current_exit: 7/9 pass, 2 fail (oos, param_stability)
- fast_exit: 7/9 pass, 2 fail (cost_stress, oos)
- hybrid_exit: 7/9 pass, 2 fail (oos, param_stability)
- All 3 modes: 0 ERROR
- OOS fail = data quantity (test window too short for 30+ trades)

### Final Conclusion (unchanged)

FastExit: **remain_research_exit_mode**
HybridExit: **promising_research_lead** (requires preregistered validation)
MinSwing v3 current_exit: **remain production default**
