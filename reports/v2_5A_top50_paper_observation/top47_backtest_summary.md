# Top47 MinSwing v3 Backtest Summary

> Date: 2026-04-24
> Strategy: MinSwing v3 (default exit config, no per-coin optimization for new coins)
> Data: 90 days 5m, OKX Spot USDT
> Cost model: OKX_SWAP (maker 0.02%, taker 0.05%, 3bp slippage)
> Init cash: $50 per coin

## Portfolio Overview

| Metric | Value |
|--------|-------|
| Coins tested | 47 / 47 (100%) |
| Total invested | $2,350 |
| Total P&L | **+$30.95 (+1.3%)** |
| Profitable coins | 20 / 47 (43%) |
| Avg win rate | 25.4% |
| Total trades | 3,674 |

## Tier Summary

| Tier | Sharpe Range | Coins | Total P&L | Avg Sharpe | Description |
|------|-------------|-------|-----------|------------|-------------|
| **A** | >= 2.0 | 7 | **+$109.15** | +2.609 | Production candidates |
| **B** | 0.5 — 2.0 | 9 | +$57.29 | +1.260 | Worth monitoring |
| C | 0 — 0.5 | 6 | +$1.94 | +0.263 | Marginal edge |
| D | < 0 | 25 | -$137.44 | -1.188 | Strategy does not work |

## Tier A (7 coins — Production candidates)

| Symbol | Return | Sharpe | WR | PF | AvgWin | AvgLoss | Trades |
|--------|--------|--------|-----|-----|--------|---------|--------|
| CFX-USDT | +43.9% | +3.43 | 28.2% | 1.81 | +4.05% | -0.88% | 78 |
| ETH-USDT | +27.1% | +3.17 | 29.3% | 1.69 | +2.64% | -0.65% | 82 |
| NEAR-USDT | +25.8% | +2.61 | 35.5% | 1.54 | +2.74% | -0.98% | 76 |
| PENGU-USDT | +30.9% | +2.45 | 29.3% | 1.49 | +4.21% | -1.17% | 75 |
| ENJ-USDT | +43.5% | +2.32 | 23.4% | 1.58 | +6.89% | -1.34% | 77 |
| DYDX-USDT | +29.9% | +2.21 | 31.2% | 1.47 | +3.86% | -1.19% | 77 |
| SOL-USDT | +17.2% | +2.06 | 31.4% | 1.45 | +2.64% | -0.84% | 70 |

## Tier B (9 coins — Worth monitoring)

| Symbol | Return | Sharpe | WR | PF | Trades |
|--------|--------|--------|-----|-----|--------|
| ARB-USDT | +18.7% | +1.83 | 28.9% | 1.35 | 83 |
| MASK-USDT | +19.8% | +1.77 | 32.5% | 1.41 | 77 |
| ONDO-USDT | +14.0% | +1.50 | 28.4% | 1.35 | 74 |
| ENA-USDT | +14.2% | +1.44 | 34.7% | 1.27 | 72 |
| PYTH-USDT | +12.2% | +1.24 | 26.0% | 1.26 | 77 |
| OKB-USDT | +16.9% | +1.02 | 15.7% | 1.51 | 83 |
| TRUMP-USDT | +7.9% | +1.01 | 28.1% | 1.22 | 64 |
| DOGE-USDT | +6.5% | +0.92 | 28.9% | 1.17 | 76 |
| HYPE-USDT | +4.5% | +0.61 | 27.6% | 1.10 | 87 |

## Key Findings

1. **Strategy is NOT universally applicable** — only 43% of coins are profitable
2. **Tier A+B concentration** — 16 coins generate +$166.44, the other 31 lose -$135.50
3. **Original 4 production coins**: ETH/SOL/NEAR in Tier A, **ARB in Tier B** (Sharpe +1.83)
4. **New Tier A discoveries**: CFX (+43.9%), PENGU (+30.9%), ENJ (+43.5%), DYDX (+29.9%)
5. **BTC is Tier D** (Sharpe -0.90) — MinSwing does not work on BTC 5m
6. **If only trade Tier A**: +$109 on $350 = **+31.2%**
7. **If trade Tier A+B**: +$166 on $800 = **+20.8%**

## Data Note

- New coins use default exit config (trailing 2%, SL 2%), no per-coin optimization
- 5 coins (DOGE/XRP/ADA/LINK/AVAX) were initially missing 5m data, now backfilled
- ARB is Tier B (Sharpe +1.833), NOT Tier A — corrected from earlier report
