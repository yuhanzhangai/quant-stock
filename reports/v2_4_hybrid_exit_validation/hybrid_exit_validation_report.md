# v2.4 Hybrid Exit Candidate Validation Report

> Date: 2026-04-24
> Experiment: hybrid_exit_candidate_validation (preregistered)
> Symbol: ETH-USDT 5m
> Data: 26,203 rows, freshly refreshed

## Hypothesis

在保持 MinSwing v3 entry logic 完全不变的情况下，hybrid_exit 可以改善 ETH-USDT 的收益回撤特征。

## Entry-Level Paired Replay

| Exit Mode | Avg Return/Trade | Avg Holding |
|-----------|-----------------|-------------|
| current_exit | +0.4051% | 74 bars |
| hybrid_exit | +0.4536% | 67 bars |

Hybrid advantage: +0.049%/trade, 7 bars shorter holding.

## Portfolio-Constrained Replay

| Metric | current_exit | hybrid_exit | Better? |
|--------|-------------|-------------|---------|
| Final Equity | $63.26 | $65.58 | hybrid |
| Net Return | +26.5% | +31.2% | hybrid (+4.6pp) |
| Profit Factor | 2.37 | 2.53 | hybrid |
| Max Drawdown | -5.8% | -5.2% | hybrid |
| Fee/Gross | 34.7% | 32.1% | hybrid |
| Avg Holding | 77 bars | 69 bars | hybrid |

## Windowed Replay

| Window | current_exit Return | hybrid_exit Return | Hybrid Better? |
|--------|--------------------|--------------------|----------------|
| full_sample | +26.5% | +31.2% | YES |
| recent_90d | +26.6% | +31.2% | YES |
| recent_60d | +20.6% | +23.7% | YES |
| recent_30d | -3.2% | -3.1% | MARGINAL (both negative) |

**3/4 windows favorable** (full, 90d, 60d clear wins; 30d marginal but hybrid still less negative).

## Cost Stress

| Mode | Normal Return | Pessimistic Return | Survives |
|------|-------------|-------------------|----------|
| current_exit | +26.5% | +15.4% | YES |
| hybrid_exit | +31.2% | +19.5% | YES |

Hybrid survives both cost scenarios and maintains advantage.

## 9-Gate Validation (aligned objects)

| Gate | current_exit | hybrid_exit |
|------|-------------|-------------|
| data_quality | PASS | PASS |
| baseline_backtest | PASS (1.31) | PASS (1.44) |
| cost_stress | PASS (1.31) | PASS (1.44) |
| oos | FAIL (trades<30) | FAIL (trades<30) |
| walk_forward | PASS (0.60) | PASS (0.60) |
| random_baseline | PASS (+13.3%) | PASS (+18.9%) |
| monte_carlo | PASS (72%) | PASS (79.5%) |
| event_backtest | PASS (-11.8%) | PASS (-11.3%) |
| parameter_stability | FAIL (0.40) | FAIL (0.40) |

Both: 7/9 pass, 2/9 fail, **0 ERROR**.
OOS fail = data quantity (not quality). parameter_stability = shared entry params.
Hybrid scores higher on 6/9 gates.

## Judgment Criteria

| Criterion | Required | Result | Pass? |
|-----------|----------|--------|-------|
| ≥3/4 windows favorable | ≥3 | 3/4 | **PASS** |
| Profit factor improvement | Yes | 2.53 vs 2.37 | **PASS** |
| Max drawdown not worse | Not worse | -5.2% vs -5.8% | **PASS** |
| Fee/gross not worse | Not worse | 32.1% vs 34.7% | **PASS** |
| Pessimistic cost survives | Yes | +19.5% | **PASS** |
| 9-gate no ERROR | 0 | 0 | **PASS** |
| Random baseline PASS | PASS | PASS (+18.9%) | **PASS** |

**7/7 criteria passed.**

## Decision

### **`promote_to_shadow_paper`** (with caveats from A2 review)

### Reasoning

1. Hybrid_exit passes ALL 7 formal judgment criteria
2. Improves return (+4.6pp), PF (+0.16), drawdown (+0.6pp), fee ratio (-2.6pp)
3. Consistent across 3/4 time windows
4. Survives pessimistic cost stress
5. 0 gate errors, scores higher than current on 6/9 gates
6. Monte Carlo: 79.5% profit probability (vs 72% current)

### A2 Review Caveats (must be addressed in v2.5)

A2 flagged the following concerns that do NOT block shadow paper but MUST be monitored:

1. **Not truly independent data**: v2.4 entry set is identical to v2.3R (same underlying data,
   only a few new candles added). True out-of-sample will come from v2.5 shadow paper with
   genuinely new data over 14+ days.

2. **30d window drawdown worse**: hybrid -4.77% vs current -4.31%. The "3/4 favorable"
   count relies on the 30d window being "marginal." In the most recent period, hybrid
   shows worse drawdown and fee/gross > 100%. v2.5 must monitor this.

3. **Trade concentration risk**: Top 5 trades = 78.8% of total hybrid PnL. Only 12/84
   trades show any difference between hybrid and current. The edge is thin and concentrated.

4. **30d hybrid = fast_exit behavior**: In recent 30 days, hybrid degenerates to fast_exit
   (identical metrics). Fast_exit was already rejected in v2.3R. If this regime persists,
   hybrid loses its advantage.

5. **parameter_stability FAIL**: Both modes fail (0.40 < 0.50). Entry params are fragile.

### v2.5 Shadow Paper must specifically verify:

- Does hybrid maintain advantage on genuinely new data (not the same entries)?
- Does the trade concentration persist, or does the edge broaden?
- Does hybrid continue to degenerate to fast_exit in recent regimes?
- Does the 30d drawdown concern persist?

### Next Step

v2.5: Shadow paper observation with hybrid_exit as shadow candidate.
Production default remains current_exit until v2.6 promotion review.
**Shadow paper is the true independent verification that v2.4 could not provide.**

## Artifacts

| Artifact | Location |
|----------|----------|
| Paired replay | data/research/replay/run_id=v2_4_hybrid_exit_validation/ |
| Portfolio trades | .../portfolio/exit_mode=hybrid_exit/trades.parquet |
| Portfolio metrics | .../portfolio/exit_mode=hybrid_exit/metrics.json |
| Windowed summary | .../windows/windowed_summary.csv |
| Cost stress summary | .../cost_stress/cost_stress_summary.csv |
| Experiment file | experiments/completed/20260424_hybrid_exit_candidate_validation.yml |
| DB: backtest_runs | 2 rows (exit_mode_portfolio_replay) |
| DB: validation_results | 18 rows (9 per mode) |
