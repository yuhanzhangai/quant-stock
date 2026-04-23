# 策略推荐指南

经过 35 轮迭代、34 个策略、14+ 种方法论测试后的最终推荐。

## 分钟线交易 (5m) - $50 本金 + 杠杆

### 推荐策略：MinSwing

| 币种 | 参数 | 杠杆 | 3 月收益 | 稳定性 |
|------|------|------|---------|--------|
| **ETH** | tm=180 sl=2% tp=6% gap=36 | 5x | **+123%** | 2/3 月正 |
| **SOL** | tm=180 sl=2% tp=8% gap=144 | 5x | **+86%** | **3/3 全正** |
| BTC | 不推荐 | - | -2% | 1/3 月正 |

**运行方式：**
```bash
# ETH 5m
python -c "
from src.strategies.minute_swing import minute_swing_signal
# trend_ma=180, stop_pct=2.0, take_profit_pct=6.0, min_gap=36
"
```

### 风险提示
- seg1 (极端熊市 -33%) MinSwing 亏损 sharpe -1.31
- 5x 杠杆下最大亏损约 $15 (30%)
- 每月约 10-15 笔交易

---

## 中线交易 (4h) - 无杠杆或低杠杆

### 推荐策略：DynamicSelector v5

| 币种 | OOS 夏普 | OOS 回撤 |
|------|---------|---------|
| **BTC** | **+1.559** | 4.1% |
| **ETH** | **+1.642** | 8.2% |
| **SOL** | +0.224 | 14.6% |
| **XRP** | +0.780 | 8.0% |
| **LINK** | +0.412 | 16.8% |
| **ADA** | +0.776 | 7.4% |

**6/7 币种 OOS 正。自动在 5 个 ROBUST 策略间切换。**

### 4 个通过样本外验证的 ROBUST 策略
1. **ExtremeReversal** - 大跌后抄底 (OOS +0.976)
2. **AggressiveMom** - 趋势追涨 (OOS +0.572)
3. **IchimokuMomentum** - 趋势确认入场 (OOS +0.443)
4. **TrendMA_Filtered** - 保守均线跟踪 (OOS +0.146)

---

## 长线交易 (1d)

### 推荐：AggressiveMom
- 1 年跨度 **100% 正率**
- 6 月跨度 IchimokuMom **92% 正率**

---

## 关键研究发现

1. **没有万能策略** - 不同市场情绪需要不同策略
2. **Regime detection 是核心** - 熊市用 ExtremeReversal，牛市用 AggressiveMom
3. **Per-coin 参数路由** - 每个币种需要独立优化的参数
4. **6 月是最佳评估跨度** - 短于 3 月不可靠
5. **利空事件后策略反而正** - 恐慌后反弹是可靠模式
6. **分钟线核心困难** - 手续费 + 噪音，只有极低频策略存活

## 项目统计
- 34 个策略，41 次提交，35 轮迭代
- 8 个币种，2 年 4h + 3 月 5m 数据
- 多维验证：OOS / Walk-Forward / 多跨度 / 事件回测
- Tags: v0.1.0-mvp → v0.5.0-minute-trading
