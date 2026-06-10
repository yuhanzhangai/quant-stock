# QuantLab Project Status

> 最后更新: 2026-04-24
> 项目: 研究型加密货币量化平台 (OKX)
> GitHub: https://github.com/YuhanZhangxxx/QuantLab

---

## 当前状态

```
Active:    v2.5A Top50 Paper Observation (泛用性测试)
           - v2.5A-1: Universe 选币 + 数据拉取 + 质量检查 ← 进行中
           - v2.5A-2: Top50 Paper Observation (v2.5A-1 通过后)
Deferred:  v2.5 True OOS Shadow Paper (等新数据, cutoff 2026-04-24)
Blocked:   v2.6 Evidence Review (等 v2.5)
```

## 策略状态

| 策略 | 状态 | 说明 |
|------|------|------|
| MinSwing v3 | **Production** | 唯一生产策略, Sharpe +2.13, 可复现 |
| HybridExit | conditional_shadow_candidate | v2.4 条件通过但数据不独立, 等 v2.5 OOS |
| FastExit ETH | research (降级) | 96% 入场重叠, 4/7 标准失败, 不再是候选 |
| short_session_filter | candidate_blocked | 验证管线不支持做空 |
| short_trend_follow | candidate_blocked | 同上 |
| 40+ archived | archive | 历史迭代, 3个确认过拟合 |

## 项目指标

| 指标 | 值 |
|------|-----|
| 代码行 | ~25,000 |
| Python 文件 | ~195 |
| Git commits | ~160 |
| 测试 | 49/49 pass |
| Lint | 0 errors |
| Dashboard | 10 页面 |
| DB 表 | 7 张 |
| 数据 | 24 币种, 90 天, 669K 行 |

---

## 版本历史

### v1.x 策略研究阶段

**v1.0.0-production (2026-04-22)**
- MinSwing 策略诞生, 74+ 轮迭代
- 40+ 策略测试, 大部分失败
- 建立了基础设施: CCXT 客户端, Parquet 存储, vectorbt 回测, Streamlit Dashboard

**v1.1.0-final (2026-04-22)**
- 策略研究收尾
- Monte Carlo: 72% 盈利概率
- 确认 3 个过拟合策略 (Ichimoku/MACD_Hist/MomBreakout)
- 做空策略验证 (session_filter +2.83, trend_follow +2.35)
- FastExit combo 发现 (+34% 改进)

**v1.1-research-baseline (2026-04-23)**
- 冻结基线, 作为 v2.0 改造的回滚点

---

### v2.0 研究基础设施重建

**v2.0 C0-C12 (2026-04-23)**
- 原因: v1.1 只有文件报告, 没有结构化研究数据库
- 13 个 checkpoint 完成:
  - C0: 冻结基线 (tag + branch + backup)
  - C1: 策略分类 (registry + config YAML + 状态头)
  - C2: research.duckdb (7 张表)
  - C3: 数据 manifest (90 文件, 668K 行, checksum)
  - C4: 数据质量门禁 (7 项检查, critical 阻止回测)
  - C5: 标准化回测输出 (config/metrics/trades/equity)
  - C6: 实验台账 (假设先行, 结论闭环)
  - C7: 9-gate 验证管线 (一条命令出 pass/fail)
  - C8: 成本/滑点/风险模型 (3 级压力测试 + RiskEngine)
  - C9: 策略准入门禁 (状态机 + 每级规则)
  - C10: Paper session 管理 (context manager + 自动 finalize)
  - C11: Dashboard 升级 (5 个新研究页面, 共 10 页)
  - C12: 每日工作流检查清单
- 产出: 从 20K 行升级到 42K 行, CI lint 0 errors

---

### v2.0.1 持久化加固

**v2.0.1-persistence-hardening (2026-04-24)**
- 原因: 审计发现 DB 写入可以被静默跳过, Dashboard 扫文件而非读 DB
- 7 个 fix:
  - Fix 1: src/research/db.py — connect_research_db(required=True) fail-fast
  - Fix 2: validation DB 写入失败 → 命令失败 (不只写 JSON)
  - Fix 3: 参数搜索所有组合 + 最优结果都写 backtest_runs
  - Fix 4: save_all() 去掉 write_db=False 逃逸口
  - Fix 5: PaperSession context manager + 自动 finalize + 异常记录 failed
  - Fix 6: Dashboard 页面 4 从 DB 索引 (不再 glob HTML)
  - Fix 7: Dashboard 页面 10 从 DB 索引 (不再 glob 目录)
- 结果: research.duckdb 成为研究数据单一真相源

---

### v2.1 验证加固

**v2.1-validation-hardening (2026-04-24)**
- 原因: 需要证明研究平台不只是生成漂亮报告, 也能复现好策略/拒绝坏策略
- 4 步完成:
  - Step 1: 47 个 data quality warning 全部解释 (44 延迟 + 3 真实市场事件)
  - Step 2: MinSwing v3 可复现 (3 次运行完全一致: Sharpe=3.265787, trades=81)
  - Step 3: Gate 判断力验证 (overfit 策略被 6/9 gate 拒绝, 好策略通过)
  - Step 4: Candidate 审查 (FastExit/short 全部 remain_candidate)
- Paper calibration deferred → v2.2

---

### v2.2 Paper 校准

**v2.2-paper-calibration (2026-04-24)**
- 原因: 需要用真实 OKX API 数据验证回测假设是否成立
- 完成:
  - API preflight: 4/4 PASS
  - 数据刷新: ETH/SOL/NEAR/ARB +1099 candles
  - MinSwing v3 paper: ETH +24.5%, SOL +9.4%
  - FastExit ETH paper: +27.1%
  - RiskEngine: 18 rejections (6.6%), 0 false kills
  - 信号匹配: 93 vs 92 (1.1% 偏差)
  - 成本: ~42% gross edge, 可存活
- 结论: MinSwing v3 remain_production, FastExit remain_candidate

---

### v2.3 Paper 观察 + 历史 Replay

**v2.3-paper-observation-hardening (2026-04-24)**
- 原因: 需要建立连续观察机制, 验证 FastExit 与 MinSwing 的关系
- 完成:
  - C0-C6: 观察配置, paper sessions, overlap 分析, RiskEngine 审计
  - **关键发现: FastExit 与 MinSwing 96% 入场重叠 → exit_variant_only**
  - Integrity Gate: backtest_runs=0 修复 (ALTER TABLE), random_baseline=ERROR 修复 (dtype bug)
  - 9-gate 从 8/9+1ERROR 变为 9/9 fully operational

**v2.3R Historical Replay Maturity (2026-04-24)**
- 原因: 用历史数据判断 FastExit 是否值得作为 MinSwing exit_mode
- 9 个 phase:
  - Phase 1-2: 基线冻结 + replay 配置
  - Phase 3: common entry generator (确定性 entry_id, 5 个测试)
  - Phase 4: entry-level paired replay (4 种 exit_mode 逐笔对比)
  - Phase 5-7: portfolio replay + windowed + cost stress
  - Phase 8: 9-gate validation (对齐对象)
  - Phase 9: 决策报告
- **结果:**
  - FastExit: +0.009%/trade (噪音), 4/7 标准失败 → remain_research_exit_mode
  - **HybridExit (意外发现)**: +31.2% vs current +26.5%, PF 2.53, DD -5.2% → promising_research_lead
- FastExit 从 candidate 降级为 research

**v2.3R.1 Compliance Fix (2026-04-24)**
- 原因: v2.3R Phase 5-8 有合规偏离 (artifact 未持久化, DB 未写入, gate 对象不一致)
- 7 个 fix (A1-A7): trail 参数修复, 4 个新测试, portfolio artifact 持久化, windowed/cost summary, 9-gate 对象对齐
- 测试从 45 增加到 49

---

### v2.4 HybridExit 候选验证

**v2.4-hybrid-exit-candidate-validation (2026-04-24)**
- 原因: v2.3R 发现 HybridExit 表现最优, 但它是意外发现, 需要正式 preregistered validation
- 路线变更: 原计划 v2.4 = FastExit integration → 改为 HybridExit validation (FastExit 已证明不够格)
- 完成:
  - Preregistered experiment 创建 + 结束 (accepted)
  - Paired replay: hybrid +0.049%/trade
  - Portfolio: $65.58 vs $63.26 (+4.6pp)
  - Windowed: 3/4 favorable
  - Cost stress: 两者都存活
  - 9-gate: 0 ERROR, 7/9 pass
  - 7/7 形式标准通过
- A2 审核发现 5 个重大 caveat:
  1. 数据不独立 (100% entry_id 与 v2.3R 重叠)
  2. Trade concentration (top 5 = 78.8% PnL)
  3. 只 12/84 笔有差异
  4. 30d hybrid drawdown 更差
  5. parameter_stability 双双 FAIL

**v2.4.1 Decision Amendment (2026-04-24)**
- 原因: v2.4 形式通过但 A2 证明不是独立验证
- 修正:
  - promote_to_shadow_paper → **conditional_promote_to_shadow_paper**
  - v2.4 定性为 reproducibility confirmation, 不是 independent validation
  - Registry: hybrid_exit = conditional_shadow_candidate, 5 个 caveat 全部记录
  - v2.5 门槛收紧: concentration≤60%, differential≥30, drawdown gate, fee/gross gate
  - v2.6 改名 Evidence Review (不是 Promotion Review)

---

### v2.5 True OOS Shadow Paper

**v2.5-oos-deferred (2026-04-24)**
- 原因: 历史 90 天数据全被 v2.3R/v2.4 用完, 最近 30d overlap = 31/31 = 100%, 新 OOS = 0
- 状态: **DEFERRED**
- Cutoff: 2026-04-24T00:00:00Z
- 恢复条件: 下载 cutoff 后的新数据, 新 entry ≥ 30, overlap = 0

### v2.5A Top50 Paper Observation

**v2.5A-top50-paper-observation (2026-04-24 启动)**
- 原因: 测试 MinSwing v3 在 Top50 USDT 市场的泛用性
- 不是 v2.5 hybrid OOS，不升级策略，只观察
- v2.5A-1: Universe 选币 + 数据拉取 + 质量检查 ← 进行中
- v2.5A-2: Top50 Paper Observation (v2.5A-1 通过后)
- 选币: OKX 24h 成交量 Top50 USDT Spot (排除稳定币/杠杆/低流动性)
- 已有 17 币 + 需新拉 32 币
- 新币统一 default exit config，不做 per-coin 优化
- IPv4 强制修复已完成 (解决 OKX IPv6 白名单问题)

### v2.6 Evidence Review

- 状态: **BLOCKED** (等 v2.5)

---

## 工作准则

1. **最高准则**: 所有变更必须经 A1+A2 双子 Agent 复核后才能 commit
2. 不新增策略 / 不新增架构 / 不新增 Dashboard 页面
3. 只做验收、删减、校准、复现
4. Production 策略参数不改
5. 所有研究结果写 research.duckdb
6. 每次输出: 修改文件、DB 写入、测试结果、未完成项

## 恢复工作指南

### 继续 MinSwing v3 papertrade

```bash
python scripts/live_signal.py --once        # 检查信号
python scripts/market_health.py             # 市场健康
python scripts/run_data_quality.py --all    # 数据质量
```

### 恢复 v2.5 (30+ 天后)

```bash
python scripts/bootstrap_data.py            # 拉新数据
python scripts/build_data_manifest.py       # 重建 manifest
# 然后跑 v2.5 OOS eligibility check
# 如果 new_oos_entries >= 30 且 overlap = 0, 跑 shadow replay
```

### 版本 spec 文件

```
修改方向.md / v2.0.1-*.md / v2.1-*.md / v2.2-*.md / v2.3-*.md
v2.3R-*.md / v2.3R.1-*.md / v2.4-*.md / v2.4.1-*.md
v2.5-*.md / v2.6-*.md / MASTER_ROADMAP.md
```
