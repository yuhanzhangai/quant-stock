# Week 1 Paper vs Backtest

> Period: 2026-04-24 (day 1 of observation)
> Note: This is initial baseline, not yet a full week

## MinSwing v3 (ETH-USDT)

| Metric | Backtest | Paper | Deviation | Status |
|--------|----------|-------|-----------|--------|
| signal_count | 92 | 93 | +1.1% | normal |
| trade_count | 81 | 74 | -8.6% | normal (risk filtering) |
| rejected_ratio | 0% | 8.6% | +8.6% | normal (<20% threshold) |
| equity return | +27.9% | +24.5% | -3.4pp | normal |

## FastExit ETH

| Metric | Backtest | Paper | Deviation | Status |
|--------|----------|-------|-----------|--------|
| signal_count | 92 | 93 | +1.1% | normal |
| trade_count | — | 77 | — | — |
| rejected_ratio | 0% | 6.5% | +6.5% | normal |
| equity return | — | +27.1% | — | — |

## Cost Analysis

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| avg_slippage_bps | 3.0 | <6.0 (2x model) | normal |
| fee/gross_pnl (est) | ~42% | <50% | normal |

## Conclusion

Day 1 baseline established. All metrics within normal range. No warnings.
Full weekly comparison requires 7 days of continuous observation.
