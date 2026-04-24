# v2.5 True OOS Shadow Paper — Deferred

> Date: 2026-04-24
> Status: DEFERRED — waiting for new OOS data

## Reason

- ETH-USDT 5m history covers 2026-01-23 to 2026-04-24 (90 days)
- v2.3R / v2.4 already used ALL 93 historical entries
- Recent 30d entries overlap = 31/31 = 100%
- New OOS entries = 0
- v2.5 requires future data that does not yet exist

## OOS Cutoff

```text
cutoff_ts: 2026-04-24T00:00:00Z
rule: Only entries with entry_ts > cutoff_ts count as v2.5 true OOS
overlap_required: 0 (zero overlap with v2.3R/v2.4 entries)
```

## Strategy Status (unchanged)

```text
MinSwing v3 current_exit: production default
hybrid_exit: conditional_shadow_candidate (no change)
FastExit: research (no change)
```

## What is blocked

```text
v2.5 True OOS Shadow Paper: blocked until sufficient new entries exist
v2.6 Evidence Review: blocked until v2.5 completes
hybrid_exit promotion: blocked
hybrid_exit as optional/default/live: blocked
```

## What continues

```text
MinSwing v3 production papertrade observation: active
API / data quality monitoring: active
```

## Resume conditions

```text
1. Download new ETH-USDT 5m data (after cutoff_ts)
2. Generate new MinSwing entries
3. Verify overlap_with_v2_3R = 0 and overlap_with_v2_4 = 0
4. If new_oos_entry_count >= 30: run v2.5 historical OOS shadow replay
5. If differential_exit_count >= 30: complete v2.5 and proceed to v2.6
```

## Estimated timeline

- MinSwing avg ~1 entry/day
- Need ~30 new OOS entries minimum
- Earliest resume: ~30 days from cutoff (late May 2026)
- For differential_exit_count >= 30 at 14% rate: ~7 months
