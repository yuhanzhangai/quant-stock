# Baseline Test Result

## Test Date
2026-04-23

## Environment
- Python 3.12.10
- pytest 9.0.3
- Platform: Windows 11 Pro (win32)
- pytest-asyncio: mode=auto

## Results

| Metric | Value |
|--------|-------|
| Total collected | 40 |
| Passed | 40 |
| Failed | 0 |
| Errors | 0 |
| Duration | 4.08s |

## Breakdown by Module

| Module | Tests | Status |
|--------|-------|--------|
| tests/exchange/test_ccxt_client.py | 4 | All PASSED |
| tests/exchange/test_okx_client.py | 4 | All PASSED |
| tests/exchange/test_rate_limiter.py | 5 | All PASSED |
| tests/factors/test_technical.py | 9 | All PASSED |
| tests/storage/test_duckdb_client.py | 4 | All PASSED |
| tests/storage/test_parquet_writer.py | 6 | All PASSED |
| tests/storage/test_state_tracker.py | 8 | All PASSED |

## Failed Tests
None

## Impact on Main Flow
No issues. All core modules (exchange, storage, factors) are fully functional.

## CI Status (GitHub Actions)

| Check | Status | Details |
|-------|--------|---------|
| pytest | PASS | 40/40 tests pass locally |
| ruff check | FAIL | 140 lint errors (80 auto-fixable) |
| ruff format | Not checked | Likely has issues |

CI failure is due to accumulated lint issues across strategy files, not test failures.
This will be addressed after Checkpoint 0 is frozen.

## Notes
- All tests use mock/fixture data (no live API calls)
- Test fixtures in tests/fixtures/ contain real OKX API response samples
- asyncio_mode = "auto" configured in pyproject.toml
