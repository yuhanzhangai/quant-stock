# Current State

> Baseline snapshot taken at v1.1-research-baseline (2026-04-23)

## Version

- Tag: `v1.1-research-baseline`
- Branch: `stable/v1.1-research-baseline`
- Commit: 83dd624
- Total: 170 Python files, 20,992 lines of code
- Git history: 90+ commits, tags v0.1.0-mvp through v1.1.0-final

## Data

| Dataset | Symbols | Timeframes | Coverage |
|---------|---------|------------|----------|
| OHLCV Spot | 24 coins | 4h | ~2 years (2024-2026) |
| OHLCV Spot | 24 coins | 5m | ~3 months |
| OHLCV Spot | ETH, SOL | 1m | ~14 days |
| OHLCV Spot | ETH, BTC | 15m, 1h, 1d | varies |
| Funding Rate | BTC-USDT-SWAP, ETH-USDT-SWAP | - | ~3 months |

Total data size: ~13MB Parquet

### Coin Universe (24)

AAVE, ADA, ARB, ATOM, AVAX, BTC, CRV, DOGE, DOT, ETH, FIL, INJ, LDO, LINK, NEAR, OP, PEPE, RENDER, SOL, SUI, TIA, UNI, WLD, XRP

## Production Candidates

### Long 5m (Top 3)

| # | Strategy | File | Sharpe | Notes |
|---|----------|------|--------|-------|
| 1 | MinSwing v3 | src/strategies/minswing_v3_final.py | +2.13 | ETH/SOL/NEAR/ARB, per-coin trailing/fixed exit |
| 2 | MinSwing Dual | src/strategies/minute_swing_dual.py | +2.08 | Short-signal exit enhancement |
| 3 | ExtremeReversal | src/strategies/extreme_reversal.py | +0.98 | Crash dip buying, -5% threshold ETH |

### Short 5m (Top 3)

| # | Strategy | File | Sharpe | Notes |
|---|----------|------|--------|-------|
| 1 | session_filter | src/strategies/short/short_session_filter.py | +2.83 | Exclude UTC 13-20, stop=3% |
| 2 | trend_follow | src/strategies/short/short_trend_follow.py | +2.35 | SOL/SUI/ATOM only |
| 3 | swing_trail | src/strategies/short/short_swing_trail.py | +1.10 | Trailing stop short |

### 4h Medium-term (OOS Validated)

| # | Strategy | OOS Sharpe | Overfit? |
|---|----------|-----------|----------|
| 1 | AggressiveMom | +0.572 | ROBUST |
| 2 | IchimokuMomentum | +0.443 | ROBUST |
| 3 | TrendMA_Filtered | +0.146 | ROBUST |

### Combo

| # | Strategy | File | Sharpe | Notes |
|---|----------|------|--------|-------|
| 1 | FastExit ETH | src/strategies/combo/fast_exit.py | +3.753 | ETH only, +34% vs baseline |

## Strategy Library

| Directory | Count | Status |
|-----------|-------|--------|
| src/strategies/ (root) | 7 files | Active production + base classes |
| src/strategies/long/ | 40 strategies | Archive (iteration history) |
| src/strategies/short/ | 6 strategies | 2 production candidates |
| src/strategies/combo/ | 3 strategies | 1 production candidate (FastExit) |
| src/strategies/meta/ | 6 strategies | Research (routing/ensemble) |
| src/strategies/us_stock/ | 1 strategy | Experimental (TSLA) |

## Research Scripts (44 total)

### Validation

- scripts/out_of_sample_test.py — OOS validation (exposed 3 overfit strategies)
- scripts/walk_forward.py — Walk-forward validation
- scripts/validate_3seg.py — 3-segment time-slice validation
- scripts/generalization_test.py — Cross-symbol generalization
- scripts/rolling_regime_test.py — Rolling market regime
- scripts/monte_carlo.py — Monte Carlo (72% profit prob, median +28%)
- scripts/event_backtest.py — Major event period testing
- scripts/signal_quality.py — Signal quality by trend strength

### Signal & Trading

- scripts/live_signal.py — 5m long signal scanner (confidence scoring)
- scripts/live_signal_dual.py — Long + short signal scanner
- scripts/live_signal_1m.py — 1-minute precision scanner
- scripts/paper_trader.py — Virtual P&L tracking + Telegram
- scripts/tournament_live.py — 32-strategy simultaneous paper trading

### Monitoring

- scripts/market_health.py — Daily health check (traffic light)
- scripts/strategy_monitor.py — 1-month rolling strategy health
- scripts/daily_report.py — Daily market overview

### Data

- scripts/bootstrap_data.py — Initial 2yr data backfill
- scripts/verify_okx.py — OKX API connectivity test

## Dashboard

| Page | File | Function |
|------|------|----------|
| 1 | 1_市场总览.py | Market overview, price rankings, volume chart |
| 2 | 2_资金费监控.py | Funding rate history, anomaly detection |
| 3 | 3_因子表现.py | Factor analysis, Z-score table |
| 4 | 4_回测查看.py | HTML report browser |
| 5 | 5_策略回测.py | Full K-line chart + interactive backtest |

## Infrastructure

| Layer | Files | Status |
|-------|-------|--------|
| Exchange | ccxt_client, okx_client, rate_limiter, whale_detector | Working |
| Storage | parquet_writer, duckdb_client, state_tracker | Working |
| Factors | 6 technical factors + derivatives | Working |
| Backtest | vectorbt engine, costs, metrics, reports, position_sizing | Working |
| Notify | Telegram async | Working |
| Analysis | market_events (12 preset events) | Working |

## Key Research Findings

1. **Trend MA is king**: Single most important factor (+6.2 sharpe contribution)
2. **5m markets near-random**: Hurst exponent ~0.49
3. **Risk management > signals**: TP/SL settings matter more than entry conditions
4. **Overfit trap**: 40+ strategies, only 3 survived OOS (Ichimoku, MACD_Hist, MomBreakout were FRAUD)
5. **Per-coin routing**: Different coins need different strategies/params
6. **Long and short can't combine**: Same trend MA causes signal mutual exclusion
7. **Session filter**: Excluding UTC 13-20 boosts short sharpe by +0.87
8. **Simple > complex**: More filters != better performance

## Monte Carlo Reality Check

| Metric | Value |
|--------|-------|
| Profit probability | 72% |
| Median return | +28% |
| Worst 5% | -39% |
| Win rate | 34% |
| Profit factor | 3.3:1 |

## Known Problems

1. Backtest results scattered across reports/ (no unified storage)
2. No experiment ledger (no hypothesis-driven research)
3. No data_manifest (can't track which data version produced which result)
4. No unified validation pipeline (gates are separate scripts)
5. Paper trading not linked to research results
6. No cost/slippage stress testing in standard workflow
7. Strategy parameters hardcoded in Python files (not config-driven)
8. Tournament/iteration scripts run unconstrained
9. 40+ archived strategies clutter the project
10. No strategy state machine (production/candidate/research/archive)

## Databases

| File | Purpose |
|------|---------|
| data/meta.sqlite | Ingestion state tracking (last timestamp per symbol/tf) |
| data/paper_trades.sqlite | Paper trading records |

## Configuration

| File | Purpose |
|------|---------|
| .env | OKX API credentials (read-only) |
| config/settings.py | pydantic-settings, flat config |
| config/okx.yaml | Per-endpoint rate limiting |
| config/universe.yaml | Trading pair selection rules |
