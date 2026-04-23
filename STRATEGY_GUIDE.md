# Strategy Guide (Audited Final)

## Short 5m — Per-Coin Routing

| Coin Group | Strategy | Sharpe | Why |
|------------|----------|--------|-----|
| **SOL/SUI/ATOM** | trend_follow | +2.27 | Session filter hurts these coins |
| **ARB/NEAR/FIL/DOT/PEPE/OP/ETH** | session_filter (stop=3%) | +2.83 | Exclude UTC 13-20 |

## Long 5m

| # | Strategy | Sharpe | Notes |
|---|----------|--------|-------|
| 1 | MinSwing v3 | +2.13 | ETH/SOL/NEAR/ARB, tp=8% gap=144 |
| 2 | MinSwing Dual | +2.08 | Short-signal exit enhancement |
| 3 | ExtremeReversal | +0.98 | Crash dip, -5% threshold for ETH |

## 4h Medium-term (OOS Validated)

| # | Strategy | OOS Sharpe | Overfit? |
|---|----------|-----------|----------|
| 1 | AggressiveMom | +0.572 | ROBUST (test > train!) |
| 2 | IchimokuMomentum | +0.443 | ROBUST (degrad 0.11) |
| 3 | TrendMA_Filtered | +0.146 | ROBUST (degrad 0.13) |

## OVERFIT (removed from production)

| Strategy | Backtest | OOS | Verdict |
|----------|----------|-----|---------|
| Ichimoku standalone | +0.89 | -0.37 | FRAUD |
| MACD_Hist | +0.69 | -0.69 | FRAUD |
| MomBreakout | +1.15 | -0.68 | FRAUD |
| RSIExtreme | +0.72 | -0.26 | WEAK |

## Key Audit Findings

1. **session_filter stop=7% is deceptive** — 5x leverage x 7% = 35% loss per trade. Use stop=3% (+2.83 sharpe, safer)
2. **session vs trend_follow are complementary** — per-coin routing is optimal
3. **Only 3 strategies survived ALL validation**: AggressiveMom, IchiMom, TrendMA_Filt
4. **Trend MA is the single most important factor** (+6.2 sharpe contribution)
5. **5m markets are near-random** (Hurst ~0.49), risk management > signal quality

## Production Scripts

```bash
# Daily workflow
python scripts/market_health.py          # Check traffic light + momentum
python scripts/strategy_monitor.py       # Strategy health (1-month window)
python scripts/live_signal.py --once     # Long signals
python scripts/live_signal_dual.py --once # Long + Short signals
python scripts/paper_trader.py           # Virtual P&L tracking
python scripts/tournament_live.py        # 32-strategy tournament
```

## Monte Carlo Reality Check

| Metric | Value |
|--------|-------|
| Profit probability | 72% |
| Median return | +28% |
| Worst 5% | -39% |
| Win rate | 34% |
| Profit factor | 3.3:1 |
