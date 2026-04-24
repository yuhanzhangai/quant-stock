# Strategy Card: Short Session Filter

## Status
Candidate

## Hypothesis
排除 UTC 13-20（美国午盘）的做空信号可以显著提升做空策略表现，因为该时段波动方向不利于做空。

## Universe
ARB-USDT, NEAR-USDT, FIL-USDT, DOT-USDT, PEPE-USDT, OP-USDT, ETH-USDT

## Excluded (use trend_follow instead)
SOL-USDT, SUI-USDT, ATOM-USDT

## Timeframe
5m

## Entry
- MA death cross (fast=84, slow=180)
- MACD bearish cross
- Price below both MAs
- UTC 0-12 only (session filter)

## Exit
- Stop loss 3% (NOT 7% — 5x leverage × 7% = 35% loss)
- Take profit 10%
- Trailing stop 1%
- Min gap: 288 bars (24h)

## Known Strength
- Sharpe +2.83
- Session filter adds +0.87 sharpe vs no filter
- Verified by independent review

## Known Weakness
- Hurts SOL/SUI/ATOM (use trend_follow for those)
- Needs full validation pipeline
- Short strategies inherently riskier with leverage

## Validation Summary
- OOS: PENDING
- Walk-forward: PENDING
- Monte Carlo: PENDING

## v2.1 Review (2026-04-24)
- 9-gate validation: 2/9 pass, 6/9 fail
- Failure reason: validation pipeline does not support short strategies (vectorbt long-only)
- This is a framework limitation, not a strategy quality issue
- Decision: remain_candidate (validation_status = blocked)
- Promotion blocked until short-strategy validation support is added

## Config
config/strategies/short_session_filter.yml
