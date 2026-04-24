# Top50 Backtest Coverage

> Date: 2026-04-24

## Universe: 60 candidates → 47 tested

| Stage | Count | Details |
|-------|-------|---------|
| OKX Top60 by volume | 60 | Initial scan |
| Excluded (stablecoin/gold/spread/new) | 11 | KAT, USDG, CORE, XAUT, BIO, OFC, PAXG, MON, MERL, HUMA, WLFI |
| Excluded (insufficient history) | 2 | BASED (100 bars), ROBO (100 bars) |
| **Tested** | **47** | **100% of eligible universe** |

## Data Status

| Category | Count | Notes |
|----------|-------|-------|
| Had 5m data from v1.x | 17 | Original 24 coins (some only had 4h) |
| Newly fetched for v2.5A | 30 | 90 days 5m via fetch_top50_data.py |
| Backfilled (initially missing) | 5 | DOGE, XRP, ADA, LINK, AVAX |
| Data quality | 81/81 pass | 0 critical, 0 failed |

## Coverage: 47/47 = 100%

All 47 eligible symbols have been backtested with MinSwing v3.
No symbol was skipped or partially tested.
