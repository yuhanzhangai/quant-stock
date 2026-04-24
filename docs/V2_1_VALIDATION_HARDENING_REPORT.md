# v2.1 Validation Hardening Report

> Status: **CORE PASSED, PAPER CALIBRATION DEFERRED**
> Date: 2026-04-24
> Tag: v2.1-validation-hardening

## Steps Completed

| Step | Status | Evidence |
|------|--------|----------|
| 1. Data Quality Warning Review | DONE | reports/data_quality/warning_review_20260424.md |
| 2. MinSwing v3 Reproducibility | DONE | reports/v2_1_acceptance/reproducibility_check.md |
| 3. Gate Sanity Check | DONE | reports/v2_1_acceptance/gate_sanity_check.md |
| 4. Candidate Review | DONE | reports/v2_1_acceptance/candidate_review.md |
| 5. Paper Calibration | DEFERRED | API verified OK; requires live running window → v2.2 |
| 6. Release | DONE | tag v2.1-validation-hardening |

## Core Results

### Data Quality
- 47 warnings reviewed, 0 unexplained
- 44 latest_bar_delay (batch fetch staleness, accepted_minor_gap)
- 3 price_jump (real market events: ADA rally, FIL breakout, PEPE meme, accepted_market_event)

### Production Reproducibility
- MinSwing v3 on ETH-USDT 5m: 3 runs identical
- Sharpe=3.265787, trades=81, return=27.914449%
- Deterministic: no random components

### Gate Discrimination
- Overfit strategies (Ichimoku, MACD_Hist): correctly rejected, 6/9 gates fail
- Production (MinSwing v3): 6/9 pass, 2 fail (OOS trade_count < 30 due to short test window, parameter_stability 0.4 < 0.5)
- Candidate (FastExit ETH): 7/9 pass, best performer

### Candidate Decisions

| Strategy | Decision | Reason |
|----------|----------|--------|
| FastExit ETH | remain_candidate | 7/9 gates pass, OOS fail = data quantity not quality |
| short_session_filter | remain_candidate | Framework can't validate short strategies |
| short_trend_follow | remain_candidate (blocked) | Not tested, validation pipeline limitation |

## Deferred

Paper Calibration moved to v2.2-paper-calibration:
- OKX API verified working (public, private, K-line, freshness all OK)
- Requires actual running window (days/weeks) to accumulate paper trades
- Cannot be completed in a single session

## Conclusion

v2.1-validation-hardening completed with paper calibration deferred.

The platform now proves it can:
1. Explain all data anomalies
2. Reproduce production strategy results exactly
3. Reject overfit strategies through automated gates
4. Make documented candidate decisions

What it cannot yet prove (deferred to v2.2):
- Real-time paper trading matches backtest assumptions
- Signal frequency in live matches backtest frequency
- Cost/slippage estimates are calibrated
