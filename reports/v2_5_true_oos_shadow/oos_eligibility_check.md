# v2.5 OOS Eligibility Check

> Date: 2026-04-24

## Result: NOT ELIGIBLE (historical data exhausted)

| Metric | Value |
|--------|-------|
| ETH-USDT 5m data range | 2026-01-23 to 2026-04-24 (90 days) |
| Total bars | 26,203 |
| Total entries in dataset | 93 |
| Recent 30d entries | 31 |
| overlap_with_v2_3r | 31 (100%) |
| overlap_with_v2_4 | 31 (100%) |
| **new_oos_entry_count** | **0** |
| differential_exit_count | 0 |
| **eligible_for_v2_5_historical_oos** | **FALSE** |

## Explanation

v2.3R and v2.4 used ALL 93 entries from the entire 90-day dataset.
The recent 30 days contain 31 entries — all of which are a subset of those 93.
There is zero new OOS data available from historical records.

## What this means for v2.5

v2.5 True OOS Shadow Paper **cannot** use historical data. It can only use:

1. **Future data**: entries generated from candles arriving AFTER 2026-04-24 03:35 UTC
2. **Live shadow**: run MinSwing + hybrid shadow in real-time as new candles arrive

The earliest a new entry could appear is when the next MinSwing signal triggers
on future price data (could be hours or days, depending on market conditions).

## v2.5 timeline estimate

- MinSwing min_gap = 144 bars = 12 hours between entries
- Average ~1 entry per 23 hours (93 entries / 90 days)
- To reach 30 differential exits: need ~30/0.14 ≈ 215 entries (differential rate ~14% from v2.4)
- At ~1 entry/day: **~7 months** to reach differential_exit_count >= 30

This means v2.5 with the strict differential gate may take a very long time.

## Recommendation

v2.5 Day 0 pilot should:
1. Start the shadow pipeline now
2. Accumulate new entries as they arrive
3. Report daily even if no new entries
4. Mark the v2.4 cutoff timestamp: 2026-04-24 03:35 UTC
5. Only count entries with entry_ts > 2026-04-24 03:35 UTC
