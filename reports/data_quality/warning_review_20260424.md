# Data Quality Warning Review

> Date: 2026-04-24
> Total warnings: 47
> Unexplained: 0

## Summary

| Warning Type | Count | Decision |
|-------------|-------|----------|
| latest_bar_delay | 44 | accepted_minor_gap |
| price_jump | 3 | accepted_market_event |

## latest_bar_delay (44 warnings)

All 44 warnings are `latest_bar_delay` with delay of 20-26 hours across all 24 symbols and all timeframes.

**Root cause:** Data was last fetched ~20 hours before the quality check ran. This is not a data issue — it's a staleness indicator. Data is batch-fetched, not real-time.

**Decision: `accepted_minor_gap`**

These warnings are expected in offline research mode. They would become critical only in live signal scanning (`scripts/live_signal.py`), which has its own freshness check.

**Affected symbols (all 24):**
AAVE, ADA, ARB, ATOM, AVAX, BTC, CRV, DOGE, DOT, ETH, FIL, INJ, LDO, LINK, NEAR, OP, PEPE, RENDER, SOL, SUI, TIA, UNI, WLD, XRP

## price_jump (3 warnings)

| Symbol | Date | Change | Decision | Reason |
|--------|------|--------|----------|--------|
| ADA-USDT | 2025-03-02 12:00 UTC | +30.6% | accepted_market_event | Known ADA rally |
| FIL-USDT | 2025-11-07 16:00 UTC | +31.7% | accepted_market_event | FIL breakout event |
| PEPE-USDT | 2024-11-13 12:00 UTC | +44.7% | accepted_market_event | Post-Trump-election meme coin rally |

**Verification:** All three jumps correspond to real market events visible on public charts. OHLC validity checks passed — high >= close >= low for these bars. These are not data errors.

**Decision: `accepted_market_event`**

These bars must remain in the dataset. Removing real black swan / breakout events would bias backtests toward underestimating tail risk.

## Final Status

| Metric | Value |
|--------|-------|
| Total warnings reviewed | 47 |
| accepted_market_event | 3 |
| accepted_minor_gap | 44 |
| needs_refetch | 0 |
| exclude_from_backtest | 0 |
| unexplained | 0 |
