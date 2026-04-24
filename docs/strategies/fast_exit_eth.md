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

## Config
config/strategies/fast_exit_eth.yml
