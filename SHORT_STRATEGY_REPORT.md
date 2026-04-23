# 做空策略研究报告

> 12轮迭代 | 10币种 | 4h样本外验证 | 2024-2026数据

## 最终4策略

### 1. trend_follow (冠军) — Avg Sharpe +2.35

**核心逻辑**: 双均线死叉 + MACD死叉确认，趋势跟踪做空

```
最优参数:
  fast_ma = 84-96     (短期MA, 7-8小时)
  slow_ma = 180       (长期MA, 15小时)
  min_gap = 288-336   (24-28小时最少间隔)
  stop_pct = 3.0%     (宽止损给趋势空间)
  take_profit_pct = 10%
  trail_pct = 0.8-1.0 (紧trailing锁利)
```

**币种优势** (Sharpe > 2.0, 3/3正收益):
| 币种 | Sharpe | 收益% |
|------|--------|-------|
| SOL  | +5.94  | +15.6% |
| PEPE | +4.25  | +13.2% |
| ATOM | +4.18  | +8.6%  |
| OP   | +4.07  | +12.2% |
| SUI  | +3.45  | +9.5%  |
| ETH  | +3.41  | +8.8%  |
| DOT  | +2.31  | +5.7%  |

**鲁棒性验证**:
- 4h数据样本外: 熊市段全部盈利 (SOL +7.5%, NEAR +13.0%, FIL +12.6%)
- 2026年1月大跌(-17~-22%): ETH +13.8%, SOL +14.6%, ARB +5.1%
- 2026年2月大跌(-20~-29%): SOL +25.9%, ARB +12.0%
- 非熊市: 交易极少(1-3笔), 亏损可控(-3~-5%)

---

### 2. swing_trail — Avg Sharpe +1.10

**核心逻辑**: short_swing入场 + trailing stop出场 (让利润跑)

```
最优参数:
  trend_ma = 180
  rsi_entry = 55
  min_gap = 288
  stop_pct = 2.5%
  trail_pct = 1.5%    (从低点反弹1.5%止盈)
  min_profit = 2.0%   (至少盈利2%才启动trailing)
```

20/30段正收益。SOL/SUI/ATOM上Sharpe > 2.0。

---

### 3. short_swing — Avg Sharpe +0.92

**核心逻辑**: 下降趋势 + RSI回落 + MACD死叉, 固定止盈

```
最优参数:
  trend_ma = 180, rsi_entry = 55
  min_gap = 288, stop_pct = 2.0%, take_profit_pct = 6.0%
```

**特殊价值**: ATOM上3/3正收益Sharpe=+3.63, ARB上2/3正收益。
注意: tp从8%降到6%是关键改进(+0.777 vs +0.174)。

---

### 4. rsi_overbought — FIL/NEAR专用

**核心逻辑**: RSI从超买区回落 + 布林带上轨触碰确认

```
最优参数:
  rsi_overbought = 65, rsi_entry_cross = 55
  min_gap = 192, take_profit_pct = 8.0%
```

FIL上3/3正收益Sharpe=+1.13, NEAR上微正。

---

## 币种-策略映射表

| 币种 | 最优做空策略 | Sharpe | 正收益段 |
|------|------------|--------|---------|
| SOL  | trend_follow | +5.94 | 3/3 |
| PEPE | trend_follow | +4.25 | 3/3 |
| ATOM | trend_follow | +4.18 | 3/3 |
| OP   | trend_follow | +4.07 | 3/3 |
| SUI  | trend_follow | +3.45 | 3/3 |
| ETH  | trend_follow | +3.41 | 3/3 |
| DOT  | trend_follow | +2.31 | 3/3 |
| ARB  | short_swing  | +1.32 | 2/3 |
| FIL  | rsi_overbought | +1.13 | 3/3 |
| NEAR | rsi_overbought | +0.11 | 1/3 |

## 关键发现

1. **趋势确认是做空的核心** — trend_follow靠强趋势过滤碾压所有策略
2. **追跌不工作** — momentum_break(Sharpe=-6.0)惨败,等反弹/确认再入场远优于追跌
3. **gap=288+(24h+)防过度交易** — 所有表现好的策略gap都>=288
4. **trail=0.8-1.0%紧锁利** — 做空中紧trailing优于宽TP(让利润跑但不贪)
5. **tp=6%优于tp=8%** — short_swing降低TP后Sharpe翻倍(+0.17→+0.78)
6. **策略跨时间尺度泛化** — 5m优化的参数在4h上同样有效
7. **牛市纪律良好** — 非熊市期间交易极少,亏损可控

## 迭代淘汰记录

| 策略 | 最终Sharpe | 淘汰原因 |
|------|-----------|---------|
| momentum_break | -6.05 | 追跌不工作,最差策略 |
| momentum_break_tight | -5.10 | 同上,更激进更差 |
| bounce_fade | -0.85 | 信号太少(7笔/12段) |
| ma_reject | +0.06 | 信号质量差 |
| spike_fade | -0.55 | 急涨回落做空不可靠 |

## 文件清单

```
策略文件:
  src/strategies/short_swing.py          # 基础做空 (已有)
  src/strategies/short_trend_follow.py   # 冠军策略
  src/strategies/short_swing_trail.py    # trailing版做空
  src/strategies/short_rsi_overbought.py # RSI超买做空
  src/strategies/short_bounce_fade.py    # 淘汰
  src/strategies/short_momentum_break.py # 淘汰
  src/strategies/short_ma_reject.py      # 淘汰
  src/strategies/short_spike_fade.py     # 淘汰

脚本:
  scripts/short_aggressive_backtest.py   # 初始大比拼
  scripts/short_iterate.py              # 迭代优化器
  scripts/short_robustness_test.py      # 鲁棒性验证
  scripts/short_find_bear_periods.py    # 熊市区间扫描
```

## 下一步建议

1. 将trend_follow集成到paper_trader做模拟盘验证
2. 在当前熊市(RSI=30, OI下降)实时跑信号
3. 考虑trend_follow + MinSwing v3多空组合(分配资金)
4. NEAR做空效果差,建议该币只做多不做空
