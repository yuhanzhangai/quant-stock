# QuantLab Master Roadmap

> 本文件是版本索引。每个版本的详细 spec 在根目录对应文件中。
> 最后更新：2026-04-24

## 版本文件索引

| 版本 | 文件 | 状态 |
|------|------|------|
| v2.0 C0-C12 | `修改方向.md` | DONE |
| v2.0.1 | `v2.0.1-persistence-hardening.md` | DONE |
| v2.1 | `v2.1-validation-hardening.md` | DONE |
| v2.1 收尾 | `v2.1 Closeout.md` | DONE |
| v2.2 | `v2.2-paper-calibration.md` | DONE |
| v2.2 收尾 | `v2.2-closeout.md` | DONE |
| v2.3 观察 | `v2.3-paper-observation-hardening.md` | C0-C6 DONE, C7 ongoing |
| v2.3 integrity | `v2.3-integrity-gate.md` | DONE |
| v2.3 优先级 | `v2.3-priority-plan.md` | 当前执行计划 |
| **v2.3R replay** | **`v2.3R-historical-replay-maturity.md`** | **NEXT — 9 阶段** |
| v2.4 exit-mode | `v2.4-exit-mode-integration.md` | 等 v2.3R 完成 |
| v2.5 shadow | `v2.5-shadow-paper-observation.md` | 等 v2.4 完成 |
| v2.6 决策 | `v2.6-promotion-decision.md` | 等 v2.5 完成 |

## 执行顺序

```text
v2.3R historical replay (9 阶段)  ←── 当前
    +
v2.3 C7 live observation (跨天)   ←── 并行
    ↓
v2.3 closeout
    ↓
v2.4 exit-mode integration (5 阶段)
    ↓
v2.5 shadow paper (1 阶段)
    ↓
v2.6 promotion decision (1 阶段)
```

## 核心判断主线

```text
同一批 MinSwing entry，不同 exit_mode，哪个更稳定？
```

## Claude 规则

```text
见 CLAUDE.md 最高准则 + 各版本 spec 中的"不要做"部分
```

## Master Checklist

```text
[x] v2.0.1 persistence hardening
[x] v2.1 validation hardening
[x] v2.2 paper calibration
[x] v2.3 C0-C6
[x] v2.3 Integrity Gate
[ ] v2.3 C7 live observation

[ ] v2.3R Phase 1-9 (historical replay)
[ ] v2.3 Closeout

[ ] v2.4 readiness + design + regression + interface + experiment
[ ] v2.5 shadow paper
[ ] v2.6 promotion decision
```
