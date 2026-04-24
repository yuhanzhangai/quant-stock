# FastExit vs MinSwing Overlap Analysis

> Date: 2026-04-24
> Data: ETH-USDT 5m (latest)

## Entry Overlap

| Metric | Value |
|--------|-------|
| MinSwing entries | 74 |
| FastExit entries | 77 |
| Overlap (same bar) | 71 (96%) |
| MinSwing-only | 3 |
| FastExit-only | 6 |

## Conclusion: **exit_variant_only**

FastExit ETH shares 96% of its entries with MinSwing v3. It is NOT an independent strategy — it is MinSwing v3 with a different exit rule (fast MA death cross for early profit-taking).

The +27.1% vs +24.5% performance difference comes entirely from exit timing, not from different signal generation.

## Implications

1. Running both simultaneously provides almost no diversification
2. FastExit's edge is purely in exit optimization, not signal quality
3. If FastExit were promoted to Production alongside MinSwing v3, the portfolio would have ~96% correlated positions
4. FastExit should be treated as a "MinSwing v3 exit variant" not a "second strategy"

## Recommendation

FastExit ETH remains **candidate** but should be reclassified as:
```
exit_variant_of: minswing_v3
independent_edge: false
```

If MinSwing v3's exit rules are ever reviewed (v2.4+), FastExit's exit logic should be considered as an alternative exit mode within MinSwing v3, not as a separate production strategy.
