# Strategy Card: FastExit ETH

## Status
Candidate

## Hypothesis
在 MinSwing 入场基础上，用快速 MA(90) 死叉提前退出盈利交易，可以锁定更多利润。

## Universe
ETH-USDT only

## Timeframe
5m

## Entry
Same as MinSwing v3 (trend MA + RSI + MACD)

## Exit
- Fast MA(90) death cross when profit > 0.3%
- Standard: SL 2%, TP 8%, trend reversal

## Known Strength
- Sharpe +3.753 on ETH (vs +2.13 baseline)
- +34% improvement over MinSwing v3

## Known Weakness
- ETH-only (no generalization to other coins)
- Needs more OOS validation
- Fast exit may miss larger trends

## Validation Summary
- OOS: PENDING
- Walk-forward: PENDING
- Monte Carlo: PENDING
- Event test: PENDING

## v2.1 Review (2026-04-24)
- 9-gate validation: 7/9 pass (best among candidates)
- OOS gate: pf=1.71, sharpe=3.63, but trade_count=24 < 30
- Monte Carlo: 77% profit probability
- Parameter stability: 0.6 (pass)
- Decision: remain_candidate (needs longer OOS window)
- Promotion blocked until 5m data extends beyond 3 months

## v2.2 Paper Calibration (2026-04-24)
- ETH paper: $63.57 (+27.1%), 93 signals, 77 trades, 6 rejected
- Outperformed MinSwing v3 ($63.57 vs $62.23) in this window
- Decision: **remain_candidate** — not promoted because:
  1. Single paper session = insufficient sample
  2. ETH single-coin = higher concentration risk
  3. Possible high trade overlap with MinSwing v3
  4. Needs multi-window paper observation (v2.3)

## Config
config/strategies/fast_exit_eth.yml
