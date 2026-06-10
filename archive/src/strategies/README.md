# Strategies

> ⚠️ **LEGACY 说明(2026-06-10)**:本文件为 QuantLab crypto 时代笔记,数字与状态以 `registry/strategies.yml` 为唯一事实源(本文 'Proven' 表与 registry 有出入,如 meta/dynamic_selector 在 registry 中为 archive)。long/ 与 meta/ 已移至 `archive/strategies/`。

## Proven Strategies

### Long (5m)
| # | Strategy | Sharpe | Notes |
|---|----------|--------|-------|
| 1 | minswing_v3_final | +2.4 | 5m champion, ETH/SOL/NEAR/ARB |
| 2 | minute_swing_dual | +2.1 | Short-signal exit enhancement |
| 3 | extreme_reversal | +0.98 | Crash dip buying, OOS validated |

### Short (5m)
| # | Strategy | Sharpe | Notes |
|---|----------|--------|-------|
| 1 | short/session_filter | +3.14 | CHAMPION, exclude UTC 13-20 |
| 2 | short/trend_follow | +2.35 | MA death cross + MACD |
| 3 | short/swing_trail | +1.10 | Trailing stop exit |

### 4h Medium-term
| # | Strategy | Sharpe | Notes |
|---|----------|--------|-------|
| 1 | meta/dynamic_selector | +1.05 | Auto strategy switching, 6/7 OOS |
| 2 | ichimoku_momentum | +1.1 | Trend confirmation |
| 3 | aggressive_momentum | +0.83 | Most robust, 100% on 1yr |

## Folder Structure
```
strategies/
  *.py          core files (actively imported; frozen 基线见 registry)
  short/        6 short strategies
  combo/        range + trend combo
  us_stock/     TSLA news event (cross-market experiment seed)

archive/strategies/   (repo root, moved 2026-06-10)
  long/         categorized backup of long strategies (41)
  meta/         ensemble, routing, selection (6)
```
