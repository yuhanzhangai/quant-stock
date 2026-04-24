# Paper Health Report — 2026-04-24

## Status: normal

| Check | Result |
|-------|--------|
| API | OK (public, private, K-line all working) |
| latest_bar_delay | OK (0.6 bars) |
| Paper session writing DB | OK (3 sessions in paper_sessions) |
| MinSwing v3 ETH signals | 93 |
| MinSwing v3 SOL signals | 85 |
| FastExit ETH signals | 93 |
| Rejected signals | 18 total (6.6% rate) |
| Main reject reason | cooldown_after_losses (100%) |
| Fee/slippage in fills | Yes, 3bp slippage + OKX_SWAP fees |
| Abnormal slippage | None |
| Duplicate signals | None |
| Missing exit_reason | None |

## Notes
- First day of v2.3 observation
- All sessions completed successfully
- FastExit vs MinSwing overlap = 96% (exit_variant_only)
- RiskEngine audit: risk_engine_ok
