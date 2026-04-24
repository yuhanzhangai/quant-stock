# Strategy Gate Policy

> 策略状态由规则决定，不凭感觉。

## State Machine

```
Research → Candidate → Paper Trading → Production → Retired
    ↓          ↓            ↓              ↓
  Rejected   Rejected     Retired        Retired
```

## Research → Candidate

Must satisfy ALL:

- [ ] Has experiment.yml with hypothesis
- [ ] Has fixed parameters (config/strategies/*.yml)
- [ ] data_quality gate: PASS
- [ ] baseline_backtest gate: PASS
- [ ] OOS gate: PASS
- [ ] cost_stress gate: PASS
- [ ] random_baseline gate: PASS
- [ ] trade_count >= 30
- [ ] Not effective only in a single small sample window

## Candidate → Paper Trading

Must satisfy ALL:

- [ ] walk_forward gate: PASS
- [ ] monte_carlo gate: PASS
- [ ] event_backtest: no catastrophic loss
- [ ] parameter_stability: PASS or explainable WARNING
- [ ] Risk rules defined (config/risk/*.yml)
- [ ] Stop conditions defined
- [ ] All above documented in strategy card

## Paper Trading → Production

Must satisfy ALL:

- [ ] Fixed parameters throughout paper trading (no mid-run changes)
- [ ] Paper trade fills close to backtest assumptions
- [ ] Real costs not significantly higher than backtest costs
- [ ] Signal frequency close to backtest frequency
- [ ] MAE/MFE distribution close to backtest
- [ ] No repeated/missed/delayed signals
- [ ] Minimum 2 weeks paper trading

## Production → Retired

Triggered by ANY:

- [ ] Multiple consecutive validation windows fail
- [ ] Cost increase eliminates edge
- [ ] Paper/live performance significantly deviates from backtest
- [ ] Data source change makes signals non-reproducible
- [ ] Unexplainable anomalous losses

## Current Strategy Classification

| Strategy | Status | Reason |
|----------|--------|--------|
| MinSwing v3 | Production | Passed OOS, walk-forward, Monte Carlo |
| FastExit ETH | Candidate | Needs full validation pipeline |
| short_session_filter | Candidate | Needs full validation pipeline |
| short_trend_follow | Candidate | Needs full validation pipeline |
| MinSwing Dual | Research | Too similar to v3 |
| ExtremeReversal | Research | Low trade count |
| 40+ long strategies | Archive | Historical iteration |
| Overfit strategies | Rejected | Ichimoku/MACD_Hist/MomBreakout confirmed overfit |

## Rules

1. Production strategies: max 1 primary at a time
2. Candidates: max 3 simultaneous
3. Archived strategies do not participate in daily research
4. No strategy enters production without validation_report.json
5. No parameter changes without new experiment
6. All transitions must be logged in registry/strategies.yml
