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
| v2.3 优先级 | `v2.3-priority-plan.md` | 执行计划 |
| v2.3R replay | `v2.3R-historical-replay-maturity.md` | ANALYTICAL COMPLETE |
| **v2.3R.1 合规修复** | **`v2.3R.1-replay-compliance-fix.md`** | **NEXT** |
| v2.3 closeout | (待创建) | 等 v2.3R.1 + C7 |
| v2.4 hybrid exit | `v2.4-exit-mode-integration.md` | 路线已变更 (见下) |
| v2.5 shadow | `v2.5-shadow-paper-observation.md` | 等 v2.4 |
| v2.6 决策 | `v2.6-promotion-decision.md` | 等 v2.5 |

## 路线变更 (2026-04-24)

```text
原计划:
  v2.3R → v2.4 FastExit integration → v2.5 → v2.6

变更后:
  v2.3R.1 合规修复 → v2.3 Closeout → v2.4 Hybrid Exit Validation → v2.5 → v2.6

原因:
  v2.3R 证明 FastExit 不够资格 (4/7 标准失败)
  v2.4 核心候选从 FastExit 变为 HybridExit
  HybridExit 是意外发现，需要正式 preregistered validation
```

## 执行顺序

```text
v2.3R.1 合规修复 (A1-A8)  ←── 当前
    +
v2.3 C7 live observation   ←── 并行
    ↓
v2.3 Closeout
    ↓
v2.4 Hybrid Exit Candidate Validation (preregistered)
    ↓
v2.5 Exit Mode Framework / Shadow Paper
    ↓
v2.6 Promotion Review
```

## 策略结论

```text
MinSwing v3:       remain_production
FastExit ETH:      remain_research_exit_mode (4/7 标准失败)
HybridExit:        promising_research_lead (需要 preregistered validation)
short strategies:  candidate_blocked (pipeline 不支持)
```

## Master Checklist

```text
[x] v2.0.1 persistence hardening
[x] v2.1 validation hardening
[x] v2.2 paper calibration
[x] v2.3 C0-C6
[x] v2.3 Integrity Gate
[ ] v2.3 C7 live observation
[x] v2.3R Phase 1-9 (analytical complete)
[x] v2.3R.1 compliance fix (A1-A7)
[ ] v2.3 Closeout  ←── 当前

[x] v2.4 Hybrid Exit Candidate Validation (CONDITIONAL PASS)
[x] v2.4.1 Decision Amendment (conditional_promote_to_shadow_paper)
[x] v2.5 OOS eligibility check (result: 0 new OOS, all overlap)
[-] v2.5 True OOS Shadow Paper — DEFERRED (cutoff 2026-04-24, waiting for new data)
[-] v2.6 Evidence Review — BLOCKED (waiting for v2.5)

Active track: MinSwing v3 production papertrade observation
```
