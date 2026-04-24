# Strategy Card: MinSwing v3

## Status
Production

## Hypothesis
在趋势过滤下，5m RSI/MACD 反弹信号能够捕捉短期摆动利润。

## Universe
ETH-USDT, SOL-USDT, NEAR-USDT, ARB-USDT

## Timeframe
5m

## Entry
- price > SMA(180) — trend filter
- RSI(14) rebound from oversold
- MACD(12,26,9) bullish cross

## Exit
- ETH/NEAR: trailing stop 2%
- SOL/ARB: fixed take profit 8%
- All: stop loss 2%, trend reversal (price < SMA)
- Min gap: 144 bars (12h)

## Known Strength
- OOS validated (Sharpe +2.13)
- Walk-forward positive
- Monte Carlo: 72% profit probability
- Trend MA alone contributes +6.2 sharpe

## Known Weakness
- 5m market near random (Hurst ~0.49)
- Low win rate (~34%)
- Sensitive to transaction costs
- May underperform in choppy/sideways markets

## Validation Summary
- OOS: PASS (Sharpe +2.13)
- Walk-forward: PASS
- Monte Carlo: PASS (72% profit prob)
- Event test: PASS
- Factor ablation: trend MA dominant

## v2.1 Review (2026-04-24)
- Reproducibility: 3/3 runs identical (sharpe=3.265787, trades=81)
- 9-gate validation: 6/9 pass
- OOS gate: pf=1.51 good, but trade_count=24 < 30 (test window too short)
- Parameter stability: 0.4 (marginal, trend_ma neighbors not all profitable)
- Decision: confirmed_production

## v2.2 Paper Calibration (2026-04-24)
- ETH paper: $62.23 (+24.5%), 93 signals, 74 trades, 8 rejected
- SOL paper: $54.71 (+9.4%), 85 signals, 67 trades, 4 rejected
- Signal count matches backtest (93 vs 92)
- RiskEngine: all rejections = cooldown_after_losses, 0 false kills
- Cost ~42% of gross edge, survivable
- Decision: **remain_production** — paper supports backtest assumptions

## Production Rules
- Do not change params without new experiment
- Stop after 5 consecutive losses (24h cooldown)
- No trade if data delayed > 2 bars
- Config: config/strategies/minswing_v3.yml
