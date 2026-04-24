# v2.2 API Preflight

> Date: 2026-04-24 03:13 UTC
> Commit: ab5524a
> Branch: v2.2-paper-calibration

## Results

| Check | Status | Detail |
|-------|--------|--------|
| Public API | OK | BTC/USDT last=$78,131, latency 1118ms |
| Private API | OK | USDT balance=$2.36, latency 192ms |
| ETH-USDT 5m K-line | OK | Latest bar 03:10 UTC, delay 0.6 bars |
| SOL-USDT 5m K-line | OK | Delay 0.6 bars |
| latest_bar_delay | OK | 0.6 bars < 2 bars threshold |

## Verdict

**API health = PASS**

Data freshness sufficient for paper calibration.
