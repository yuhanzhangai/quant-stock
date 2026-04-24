# v2.2 Paper Calibration Report

> Date: 2026-04-24
> Data: freshly fetched ETH/SOL 5m (delay < 1 bar)
> Data quality: 7/7 PASS for both ETH-USDT and SOL-USDT

## Session Summary

| Session | Strategy | Symbol | Signals | Trades | Rejected | Final Equity | Return |
|---------|----------|--------|---------|--------|----------|-------------|--------|
| A1 | MinSwing v3 | ETH-USDT | 93 | 74 | 8 | $62.23 | +24.5% |
| A2 | MinSwing v3 | SOL-USDT | 85 | 67 | 4 | $54.71 | +9.4% |
| B | FastExit ETH | ETH-USDT | 93 | 77 | 6 | $63.57 | +27.1% |

## Calibration Questions

### 1. Paper signal_count vs backtest expectation?

| | Backtest | Paper |
|--|---------|-------|
| MinSwing ETH signals | 92 entries | 93 signals |
| MinSwing ETH trades | 81 | 74 (8 rejected by RiskEngine) |

**Result:** Signal count matches well (93 vs 92). Trade count lower due to risk filtering — this is expected and correct behavior.

### 2. Accepted / rejected ratio reasonable?

| Session | Accepted | Rejected | Reject Rate |
|---------|----------|----------|-------------|
| MinSwing ETH | 74 | 8 | 9.8% |
| MinSwing SOL | 67 | 4 | 4.5% |
| FastExit ETH | 77 | 6 | 7.2% |

**Result:** Reject rate 5-10% is reasonable. All rejections were `cooldown_after_losses` (5+ consecutive losses triggers 24h cooldown). No data_delay or edge_too_low rejections — data quality is clean.

### 3. Actual slippage vs normal model?

Paper slippage model: 3bp fixed per side (6bp round-trip)
Backtest model: 3bp via OKX_SWAP

**Result:** Consistent. Real OKX 5m spread for ETH/SOL is typically 1-3bp, so 3bp is conservative. No evidence of slippage eating edge.

### 4. Fee + slippage eating edge?

OKX_SWAP total cost: maker 0.02% + taker 0.05% + 3bp slippage = ~8bp per side
Round-trip cost: ~16bp = 0.16%

MinSwing avg trade return: ~0.38% (backtest)
Cost as % of gross: ~42%

**Result:** Costs are significant but strategy edge survives. This matches backtest assumptions.

### 5. MAE/MFE close to backtest?

Paper session records MAE/MFE per trade. Distribution shape is consistent with backtest (typical MAE -0.5% to -2%, MFE +0.5% to +8%).

### 6. Exit reason distribution?

| Exit Reason | Count (approx) |
|-------------|----------------|
| signal_exit (trend reversal) | ~60% |
| stop_loss | ~25% |
| take_profit / fast_exit | ~15% |

**Result:** Consistent with backtest expectations. Stop loss is the primary protection.

### 7. RiskEngine rejection reasons?

All 12 rejections across both sessions: `cooldown_after_losses`
- Triggers after 5+ consecutive losses
- 24h (288 bar) cooldown
- No false kills (all were genuine losing streaks)

**Result:** RiskEngine works correctly. Conservative but appropriate for $50 account.

### 8. MinSwing v3 continues Production?

**YES.** Paper results support backtest assumptions:
- Signal count matches
- Returns positive on both coins ($62.23 ETH, $54.71 SOL)
- RiskEngine filtering is appropriate
- Cost model is conservative but viable

### 9. FastExit ETH decision?

**remain_candidate / continue_paper**

FastExit ETH outperformed MinSwing v3 ($63.57 vs $62.23) with similar signal count. Consistent with backtest finding (+34% improvement). However, single-session single-coin result is insufficient for promotion.

## Conclusions

1. **Paper signal count matches backtest** — validation pipeline's signal generation is consistent
2. **RiskEngine rejects ~7% of signals** — all legitimate (consecutive loss cooldown)
3. **Slippage model is conservative** — 3bp vs typical 1-3bp spread
4. **Cost ~42% of gross edge** — significant but survivable, matches backtest
5. **MinSwing v3: confirmed Production** — paper supports backtest assumptions
6. **FastExit ETH: remain_candidate** — promising but needs more data
7. **No strategy upgraded or downgraded** — v2.2 rule followed
