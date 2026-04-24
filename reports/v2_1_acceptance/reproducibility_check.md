# MinSwing v3 Reproducibility Check

> Date: 2026-04-24
> data_version: manifest_20260424_013050
> code_commit: fb85a25
> config: config/strategies/minswing_v3.yml
> symbol: ETH-USDT, timeframe: 5m, coin: ETH

## Results

| Run | Sharpe | Trades | Return % | Entries | Exits |
|-----|--------|--------|----------|---------|-------|
| 1 | 3.265787 | 81 | 27.914449 | 92 | 81 |
| 2 | 3.265787 | 81 | 27.914449 | 92 | 81 |
| 3 | 3.265787 | 81 | 27.914449 | 92 | 81 |

## Trade-level Verification

First 3 trade returns (all runs identical):
- [-0.00022707, -0.006231, -0.00165183]

Last 3 trade returns (all runs identical):
- [-0.00447677, 0.00850195, -0.00216788]

## Conclusion

**REPRODUCIBLE.** Same data_version + same config + same commit = same result.

MinSwing v3 is a deterministic strategy with no random components.
All metrics, trade counts, and individual trade returns match exactly across 3 runs.
