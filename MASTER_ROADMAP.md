# QuantLab Master Roadmap

> 本文件是项目后续开发的主线文档。每次实现时复制对应阶段给 Claude 执行。
> 最后更新：2026-04-24

---

## 当前状态

```text
v2.0.1: DONE — DB persistence hardened
v2.1:   DONE — Data warning reviewed, reproducibility confirmed, gate sanity confirmed
v2.2:   DONE — Paper calibration completed
v2.3 C0-C6: DONE — FastExit vs MinSwing overlap found (96%)
v2.3 Integrity Gate: DONE — backtest_runs=0 fixed, random_baseline=ERROR fixed, 9/9 gate usable

Current active work:
  v2.3 C7 live paper observation (跨天积累)
  v2.3R historical replay maturity (立即开始)
```

## 后续版本目标

```text
v2.3R → 用历史数据验证 FastExit 是否值得作为 MinSwing exit_mode 候选
v2.3 Closeout → 完成 live observation 或记录继续观察状态
v2.4 → 如果 v2.3R 支持，把 MinSwing 出场逻辑模块化，加入 exit_mode 框架
v2.5 → 对集成后的 exit_mode 做 live shadow paper observation
v2.6 → 决定是否保留、拒绝、或继续观察 fast_exit exit_mode
```

## 核心判断主线

```text
同一批 MinSwing entry，不同 exit_mode，哪个更稳定？
```

不是证明 FastExit 是独立策略，也不是马上放进 Production。

---

## Claude 每次实现必须遵守

```text
1. 一次只做一个 checkpoint
2. 不改 Production 策略参数
3. 不新增策略
4. 不把 FastExit 当独立 Production
5. 不跳过 DB 写入
6. 不绕过 data_quality gate
7. 不用文件系统代替 research.duckdb
8. 每次修改后必须跑 pytest 和 ruff
9. 所有变更必须经 A1+A2 双子 Agent 复核
10. 每次输出必须包含：修改文件、新增文件、报告、DB 写入、测试结果、未完成项
```

## Claude 通用 Prompt

```text
你现在在 QuantLab 项目中工作。请严格遵守：
- 一次只实现我指定的 checkpoint。
- 不新增策略。
- 不改 MinSwing v3 / FastExit ETH 参数。
- 不升级任何 Candidate 到 Production。
- 所有正式研究输出必须写 research.duckdb。
- 所有 artifact 必须有 DB 记录。
- 修改后运行 pytest 和 ruff。
- 所有变更必须经 A1+A2 双子 Agent 复核后才能 commit。
- 最后输出：修改文件、产出物、DB 写入、测试结果、未完成项。
```

---

## 阶段 0：建立后续开发纪律

目标：给 Claude 固定规则，避免一次性乱改太多。

规则已在上方"Claude 每次实现必须遵守"中列出。

---

## 阶段 1：v2.3R 基线冻结 (v2.3R-baseline-freeze)

目标：在历史 replay 前冻结代码、数据、策略状态。

### Todo
```text
[ ] 新建 reports/v2_3_replay_maturity/
[ ] 新建 docs/V2_3R_REPLAY_BASELINE.md
[ ] 记录当前 git commit、data_version、research.duckdb schema、策略状态
[ ] 记录 random_baseline 已修复、9/9 gate 可用
```

### 策略状态
```text
Production: MinSwing v3
Candidate: FastExit ETH, short_session_filter, short_trend_follow
Important Finding: FastExit ETH and MinSwing v3 have 96% entry overlap → exit_variant_only
```

### 给 Claude 的任务
```text
请创建 v2.3R historical replay 的基线文档和报告目录。
只新增文档和目录，不改策略代码。
文档中记录当前状态：v2.3 Integrity 已完成，random_baseline 已修复，9/9 gate 可用，
FastExit 与 MinSwing 96% 入场重叠，FastExit 只作为 exit_variant_only 研究。
```

---

## 阶段 2：设计 v2.3R Replay 配置 (v2.3R-replay-config)

目标：用配置文件定义 replay 范围。

### 新增文件
```text
config/replay/v2_3_exit_mode_replay.yml
```

### 配置内容
```yaml
version: v2.3R-historical-replay-maturity
experiment_name: minswing_exit_mode_replay
parent_strategy: minswing_v3
purpose: >
  Test whether FastExit should be treated as a MinSwing exit_mode candidate
  rather than an independent strategy.
rules:
  freeze_entry_logic: true
  freeze_strategy_params: true
  no_parameter_optimization: true
  no_strategy_promotion: true
  db_required: true
data:
  symbols: [ETH-USDT]
  timeframe: 5m
  data_version: latest
entry:
  source_strategy: minswing_v3
  entry_mode: standard
exit_modes:
  - current_exit
  - fast_exit
  - trailing_exit
  - hybrid_exit
cost_model:
  normal: true
  pessimistic: true
windows:
  - name: full_sample
    type: full
  - name: recent_30d
    lookback_days: 30
  - name: recent_60d
    lookback_days: 60
  - name: recent_90d
    lookback_days: 90
outputs:
  base_dir: data/research/replay
  report_dir: reports/v2_3_replay_maturity
```

### 不要做
```text
不要加入 SOL、不要调参数、不要新增 exit 参数搜索、不要把 FastExit 写成独立策略
```

### 给 Claude 的任务
```text
请新增 config/replay/v2_3_exit_mode_replay.yml。
配置目标是测试 MinSwing v3 entry 下不同 exit_mode 的历史表现。
第一版只测试 ETH-USDT 5m。exit_modes 包含 current_exit, fast_exit, trailing_exit, hybrid_exit。
禁止参数优化，禁止策略晋级。不要修改任何策略代码。
```

---

## 阶段 3：生成 common entry set (v2.3R-common-entry-generator)

目标：生成固定 MinSwing 入场信号，所有 exit_mode 共用。

### 关键
```text
问题不是"FastExit 和 MinSwing 哪个策略好"，
而是"同一批 MinSwing entry，用哪个 exit 更好"。
```

### 新增模块
```text
src/replay/__init__.py
src/replay/common_entries.py
```

### common_entries 字段
```text
entry_id, symbol, timeframe, entry_ts, entry_price, side, entry_reason,
entry_mode, source_strategy, source_strategy_version, params_hash,
data_version, created_at
```

### entry_id 规则
```text
entry_id = hash(symbol + timeframe + entry_ts + side + params_hash)
```
确定性——同一数据同一配置跑两次 entry_id 完全一致。

### 完成标准
```text
[ ] 一键生成 common_entries.parquet
[ ] 连续跑两次 entry_id 完全一致
[ ] common_entries 不依赖 exit logic
[ ] common_entries 有 DB experiment record
[ ] 没有改 MinSwing production 参数
```

### 测试
```text
test_common_entries_deterministic
test_common_entries_required_columns
test_common_entries_no_exit_fields
```

### 给 Claude 的任务
```text
请实现 v2.3R common entry generator。
要求：读取 config/replay/v2_3_exit_mode_replay.yml，使用 MinSwing v3 entry logic 生成固定 entry set，
输出 common_entries.parquet，entry_id 必须 deterministic，写入 experiment_runs，
DB 写入失败必须报错，不修改 MinSwing production 参数，添加测试验证重复运行 entry_id 一致。
```

---

## 阶段 4：entry-level paired replay (v2.3R-entry-level-paired-replay)

目标：对同一批 entry，分别套用不同 exit_mode，逐笔配对比较。

### 新增模块
```text
src/replay/exit_modes.py
src/replay/paired_replay.py
```

### exit_modes
```text
current_exit — 完全复刻 MinSwing v3 出场
fast_exit — FastExit ETH 出场逻辑
trailing_exit — 现有 trailing 逻辑
hybrid_exit — 简单组合（第一版标记 experimental）
```

### 输出
```text
data/research/replay/run_id=.../exit_mode=xxx/trades.parquet
data/research/replay/run_id=.../paired_exit_comparison.parquet
```

### paired_exit_comparison 字段
```text
entry_id, symbol, entry_ts, current_exit_return, fast_exit_return,
trailing_exit_return, hybrid_exit_return, current/fast/trailing/hybrid_exit_reason,
current/fast/trailing/hybrid_holding_bars, best_exit_mode, worst_exit_mode,
fast_minus_current_return, fast_minus_current_holding_bars
```

### 完成标准
```text
[ ] 所有 exit_mode 使用同一批 common_entries
[ ] 每个 exit_mode trade_count = common_entries 数量
[ ] paired_exit_comparison.parquet 存在
[ ] 可以按 entry_id 对齐比较
```

### 给 Claude 的任务
```text
请实现 entry-level paired replay。
输入 common_entries.parquet，对同一批 entries 分别运行 current_exit, fast_exit, trailing_exit, hybrid_exit。
每个 exit_mode 输出 trades.parquet，输出 paired_exit_comparison.parquet。
不允许任何 exit_mode 重新生成 entry。添加测试确认所有 exit_mode 使用完全相同的 entry_id 集合。
```

---

## 阶段 5：portfolio-constrained replay (v2.3R-portfolio-constrained-replay)

目标：加入账户约束（单 position、cooldown、RiskEngine、成本）后的 replay。

### 为什么必须做
```text
某 exit_mode 逐笔好，但可能因持仓太长导致错过后续 entry，或频繁交易导致手续费过高。
```

### 输出
```text
每个 exit_mode: trades.parquet, equity.parquet, metrics.json, rejected_entries.parquet
DB: backtest_runs (run_type=exit_mode_portfolio_replay)
```

### 给 Claude 的任务
```text
请实现 portfolio-constrained replay。
输入 common_entries 和 exit_mode logic，按 exit_mode 分别进行账户约束回放。
单 symbol 同一时间只允许一个 position，遵守 RiskEngine/cooldown/成本滑点模型。
每个 exit_mode 输出 trades/equity/metrics/rejected_entries，写入 backtest_runs。
```

---

## 阶段 6：分窗口 replay (v2.3R-windowed-replay)

目标：多个时间窗口验证 FastExit 稳定性。

### 窗口
```text
full_sample, recent_30d, recent_60d, recent_90d
```

### FastExit 进入 v2.4 的判断标准
```text
[ ] 至少 2/3 或 3/4 窗口优于 current_exit
[ ] profit_factor 提升
[ ] max_drawdown 不恶化
[ ] fee_to_gross_pnl 不显著恶化
[ ] pessimistic cost 下不崩
```

### 给 Claude 的任务
```text
请实现 windowed replay。基于 config 中的 windows 定义，对每个窗口运行 entry-level paired replay
和 portfolio-constrained replay。输出每个窗口的 exit_mode_summary.json/csv。不做参数优化。
```

---

## 阶段 7：成本压力测试 (v2.3R-exit-mode-cost-stress)

目标：确认 FastExit 不只是靠频繁退出获得表面优势。

### 成本模型
```text
normal_cost, pessimistic_cost
```

### 给 Claude 的任务
```text
请为 v2.3R exit mode replay 加入 cost stress。每个 exit_mode 跑 normal_cost 和 pessimistic_cost。
输出 cost_stress_summary.csv/json。标记 survives_cost_stress。不改变原始策略参数。
```

---

## 阶段 8：接入 9-gate validation (v2.3R-nine-gate-validation)

目标：对 current_exit 和 fast_exit 跑完整 9-gate。

### 给 Claude 的任务
```text
请把 v2.3R exit_mode replay 接入现有 9-gate validation。
至少对 current_exit 和 fast_exit 跑完整 9 gate。validation_results 必须写 DB。
任何 gate ERROR 都不能被当作 PASS。输出 validation_exit_modes.md。
```

---

## 阶段 9：v2.3R 决策报告 (v2.3R-decision-report)

目标：给 FastExit exit_mode 一个明确结论。

### 结论三选一
```text
convert_to_exit_mode_candidate — 可进入 v2.4
remain_research_exit_mode — 留在研究状态
reject_exit_variant — 拒绝
```

### 给 Claude 的任务
```text
请根据 v2.3R 所有 replay 结果生成 exit_mode_replay_maturity.md。
不要新增分析。不要改变结论标准。
结论只能是 convert_to_exit_mode_candidate / remain_research_exit_mode / reject_exit_variant。
```

---

## 阶段 10：v2.3 C7 live observation 继续 (v2.3-live-observation-continuation)

与 v2.3R 并行。每日检查 + 每周 paper_vs_backtest 对比。

### 给 Claude 的任务
```text
请为 v2.3 live paper observation 创建每日健康报告生成脚本。
脚本从 research.duckdb 和 paper session artifacts 读取数据，
输出 reports/v2_3_paper_observation/daily/YYYYMMDD_paper_health.md。
不要改策略，不要改 paper runner。
```

---

## 阶段 11：v2.3 Final Closeout (v2.3-closeout)

### 前置条件
```text
[ ] v2.3R 完成
[ ] v2.3 C7 完成或明确继续观察
[ ] Integrity Gate 已完成
[ ] 9-gate 无 ERROR
```

### 给 Claude 的任务
```text
请完成 v2.3 closeout。根据 v2.3R replay 结果和 live observation 结果，
更新 final report、registry metadata、strategy cards 和 CHANGELOG。
不要升级任何策略。不要改策略参数。
```

---

## 阶段 12：v2.4 开始条件检查 (v2.4-readiness-check)

### 必须全部满足
```text
[ ] v2.3R decision = convert_to_exit_mode_candidate
[ ] v2.3 final report 允许启动 v2.4
[ ] current_exit 可复现
[ ] fast_exit 有明确 replay 支持
[ ] 9-gate 无 ERROR
[ ] data_quality 无未解释 warning
[ ] live paper observation 无严重异常
```

---

## 阶段 13：v2.4 exit_mode 设计 (v2.4-exit-mode-design)

从 `minswing_v3 = entry + fixed exit` 改为 `minswing_v3 = entry_mode + exit_mode`。
第一步只写设计文档，不改代码。

---

## 阶段 14：v2.4 current_exit 回归保护 (v2.4-current-exit-regression-lock)

在重构前锁定当前行为。生成 baseline trades/metrics/hash，新增 regression test。

---

## 阶段 15：v2.4 exit_mode 接口实现 (v2.4-exit-mode-interface)

把出场逻辑模块化，默认行为不变。支持 current_exit（default）和 fast_exit（candidate）。

---

## 阶段 16：v2.4 exit_mode experiment (v2.4-exit-mode-experiment)

把 fast_exit 作为 MinSwing exit_mode 跑完整 9-gate 实验。
结论：promote_to_shadow_paper / remain_candidate_exit_mode / reject_exit_mode。

---

## 阶段 17：v2.5 shadow paper observation (v2.5-exit-mode-shadow-paper)

同一 MinSwing entry 下同时记录 current_exit 和 fast_exit 的 shadow outcome。不重复开仓。

---

## 阶段 18：v2.6 Promotion Decision (v2.6-exit-mode-promotion-review)

### 可能结论
```text
1. keep_current_exit_as_default
2. add_fast_exit_as_optional_candidate
3. promote_fast_exit_to_default_for_ETH
4. reject_fast_exit
```

### 最严格条件（promote 需要全部满足）
```text
[ ] v2.3R 支持
[ ] v2.4 9-gate 支持
[ ] v2.5 shadow paper 支持
[ ] 多窗口稳定
[ ] 成本压力下存活
[ ] 不恶化 drawdown
[ ] 不明显牺牲大趋势
[ ] live shadow 支持历史结论
```

---

## Master Checklist

```text
## Current
[x] v2.0.1 persistence hardening
[x] v2.1 validation hardening
[x] v2.2 paper calibration
[x] v2.3 C0-C6
[x] v2.3 Integrity Gate
[ ] v2.3 C7 live observation

## v2.3R Historical Replay Maturity
[ ] Phase 1: baseline freeze
[ ] Phase 2: replay config
[ ] Phase 3: common entry generator
[ ] Phase 4: entry-level paired replay
[ ] Phase 5: portfolio-constrained replay
[ ] Phase 6: windowed replay
[ ] Phase 7: cost stress
[ ] Phase 8: 9-gate validation
[ ] Phase 9: decision report

## v2.3 Closeout
[ ] live observation 14 days or 100 trades
[ ] final report
[ ] registry + strategy cards + changelog
[ ] tag v2.3

## v2.4 Exit Mode Integration
[ ] readiness check
[ ] design doc
[ ] current_exit regression baseline
[ ] exit_mode interface
[ ] backward compatibility test
[ ] fast_exit implementation
[ ] v2.4 experiment
[ ] v2.4 report

## v2.5 Shadow Paper
[ ] shadow paper design
[ ] shadow session implementation
[ ] 14 days or 100 shadow entries
[ ] paired shadow report

## v2.6 Decision
[ ] promotion review
[ ] registry final status
[ ] strategy cards final status
[ ] changelog
```
