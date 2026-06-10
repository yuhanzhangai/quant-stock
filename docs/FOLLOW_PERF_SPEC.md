# FOLLOW_PERF_SPEC — 跟单绩效口径(预研 v0)

> **状态:预研设计稿(spec-only,不含代码),%Valid 出品,待 %Audit 审 + Lead 会签。**
> **从属关系:ledger 字段以 `docs/ORDER_LEDGER_SPEC.md` 为准;stock-picker 字段以 `docs/INTEGRATION_NOTES.md` 为准。本文定义的是"怎么算绩效",不新增 ledger 表。**
> 事实源:stock-picker 代码实读(`src/stock_picker_mcp/trackrecord.py`、`prices.py`,2026-06-10)· `~/.stock-picker-mcp/trackrecord.db` 实查(21d evaluated 16,113 行,抽样核对口径)· ORDER_LEDGER_SPEC.md。

## 0. 目标:三个问题,三套账

| 问题 | 账 | 口径 |
|---|---|---|
| ① 我们跟到的喊单,质量如何?(信号面)| **S 账**(signal,counterfactual)| 与 stock-picker `call_outcomes` 21d **完全同口径**(§1),直接消费其评估结果 |
| ② 跟单延迟吃掉多少?(执行面固有成本)| **延迟分桶**(§3)| call_ts→submitted_ts 分桶,看 edge 随延迟的衰减——**延迟是跟单的固有成本,必须可见**(Lead 要求)|
| ③ 实际赚没赚?(组合面)| **E 账**(execution,actual)| 我们的真实成交价进出 + 同窗 SPY 基准(§2)|

诚实原则(继承反过拟合纪律):S 账好 ≠ E 账好;两账并排报告,差值有归因(§2.3),不许只展示好看的那套。所有数字可从 ledger + 只读 stock-picker 库重算复现。

## 1. S 账:与 call_outcomes 21d 对齐(代码核实的精确口径)

### 1.1 上游口径(逐条核实自 trackrecord.py / prices.py,非转述 INTEGRATION_NOTES)

| 要素 | 定义 | 代码出处 |
|---|---|---|
| horizon | **21 个交易日**(非日历日;HORIZONS=[1,5,21]) | trackrecord.py:42 |
| call_date | `call_ts`(UTC)的**UTC 日期** | trackrecord.py:254 |
| entry | call_date 之后**严格第一个交易日的收盘价**(`bisect_right`,无前视) | prices.py:181-188 |
| exit | entry 往后第 **21 个交易日**的收盘价(序列索引 i+21) | prices.py:185-189 |
| fwd_return | exit_close / entry_close − 1 | prices.py:192 |
| benchmark | **SPY** 同 entry_date→exit_date 收盘对收盘 | prices.py:203-224 |
| abnormal_return | fwd_return − benchmark_return | prices.py:227 |
| 死区 | \|abnormal\| < **0.005**(±0.5%)→ `is_hit=NULL`,不算对也不算错 | trackrecord.py:43,326-329 |
| is_hit | bullish: abnormal>0;bearish: abnormal<0 | trackrecord.py:330-333 |
| status | evaluated / pending(窗口未熟)/ no_price(无价格序列) | trackrecord.py:309-321 |
| 样本下限 | 上游 MIN_CALLS_FOR_SCORE=5 | trackrecord.py:44 |

⚠️ **UTC 跨日陷阱**:call_date 是 UTC 日期。美东周一 21:00 的帖子 = UTC 周二 → 其 counterfactual entry 是**周三收盘**;而我们可能周二开盘就跟单成交。所以 E 账入场可以**早于** S 账 entry,entry 价差可以为负(我们占优)。这不是 bug,是两套账的定义差,归因时显式呈现。

### 1.2 对齐方式:JOIN 消费,不重算

```sql
-- signals(本地 ledger)→ call_outcomes(stock-picker,只读)
SELECT s.signal_id, s.ticker, s.decision, o.*
FROM signals s
JOIN call_outcomes o
  ON o.tweet_id = s.tweet_id AND o.ticker = s.ticker AND o.horizon_days = 21
```

- **直接用他们的 entry_date/exit_date/abnormal_return/is_hit,不在 quant 侧重算**——他们用自家 prices.db 价格序列,我们重算若用不同数据源会产生假漂移(同 INTEGRATION_NOTES §1"别重算诚实榜"的纪律)。
- 重算仅作**抽查**:每期报告随机抽 ~20 条用我们的数据源复算 abnormal_return,偏差 >50bps 的比例记入报告(数据源一致性监控),不替换上游值。
- JOIN 键是 **(tweet_id, ticker)**:`call_outcomes` 主键为 (tweet_id, ticker, horizon_days),同帖多票各有独立评估行(另见 RECON_DESIGN_V0 §8.1 对 signals 表 signal_id 撞键的反馈,本文同此立场)。
- `status='pending'` 的行**不进任何汇总**(未熟);`no_price` 单列计数(上游无价,S 账缺失,E 账照报)。

## 2. E 账:实际执行绩效

### 2.1 定义(单笔跟单 = 一次开仓→平仓闭环)

- 入场:开仓单 `v_order_filled.avg_fill_price`(成交量加权,部分成交天然覆盖),入场日 = 首笔 fill 的美东交易日。
- 出场:平仓单 `avg_fill_price`,出场日同理;`exit_reason='hold_21d'`(默认退出,ORDER_LEDGER_SPEC §6,从**我们的入场日**起算 21 个交易日)。
- `actual_return = exit_avg_fill / entry_avg_fill − 1 − cost`;cost = 佣金/费用。**假设:Firstrade 模拟盘零佣金、无碎股、无做空(v1 只多头)——待 C6 实测确认,有费用则从成交确认文案/raw_text 抽。**
- 基准:`actual_abnormal = actual_return − SPY(入场日收盘 → 出场日收盘)`。**已知不精确**:我们盘中成交 vs SPY 收盘锚,日内基准误差 v0 接受并文档化;不引入盘中 SPY 数据(成本>收益)。
- 未平仓:按当日收盘 mark-to-market 报 unrealized,**单列**,不与已平仓混算。
- 股息:E 账资金曲线若页面现金含股息会被动包含,但单笔 return 不调整;S 账(价格序列是否复权未核实)与 E 账的股息处理差异列为已知误差源(§5.3)。

### 2.2 组合层

- 资金曲线:逐日 `positions_daily`(对账 PASS 的快照)市值 + 现金;日收益序列 → 累计收益、最大回撤、Sharpe(年化,√252)。**资金曲线只用对账 PASS 日的快照**——账实不符的日子,绩效数字没有资格被引用(与 RECON_DESIGN_V0 联动)。
- 报告必含:每笔盈亏分布、按 `exit_reason` 分组(hold_21d / stop_loss / direction_flip / kill_switch / manual 的笔数与盈亏——退出逻辑是我们自建的,它的贡献要可见)。

### 2.3 S−E 差值归因(每笔)

| 分量 | 定义 | 含义 |
|---|---|---|
| entry_diff_bps | (entry_avg_fill / S.entry_close − 1) × 10⁴ | 入场价差(可为负=我们更优,§1.1 UTC 陷阱)|
| window_diff | S 窗口(entry_date→exit_date)与 E 窗口(入场日→出场日)的同票收盘收益差 | 窗口错位的市场贡献 |
| early_exit_diff | exit_reason≠hold_21d 时,实际出场 vs 持满 21 交易日的差 | 自建退出逻辑的增减值 |
| cost | 费用 | v0 假设 0,待实测 |

恒等式不强求精确闭合(收盘/盘中混锚),但每笔报告四分量,总差 = S.abnormal − E.actual_abnormal 的解释覆盖率要在报告里给出。

## 3. 延迟口径与分桶(Lead 指定核心)

### 3.1 三段延迟(全部从 ledger 现有字段可算)

```text
call_ts ──(ingest_lag)──→ ingested_ts ──(engine_lag)──→ submitted_ts ──→ first_fill_ts
   └────────────── wall_latency = orders.call_to_submit_ms ──────────────┘
```

- **ingest_lag** = ingested_ts − call_ts:信号源固有(爬虫每博主约每小时一轮,发帖→入库 <1-2h,INTEGRATION_NOTES §2)+ 我们的轮询间隔。**不可控下限,这就是"跟单的固有成本"的第一段。**
- **engine_lag** = submitted_ts − ingested_ts:规则引擎+下单执行(人类节奏故意慢),我们可控。
- **actionable_latency** = submitted_ts − max(call_ts, call_ts 之后第一个美股可交易时刻):盘后/周末的帖子 wall-clock 延迟大但无操作空间,不把"市场关门"算成我们的慢。NYSE 日历 + 美东时区计算(复用研究侧交易日历,勿手写)。

### 3.2 分桶(v0 固定;改桶=升 rule_version 并在报告标注,防止换桶挑结果)

- wall_latency:`≤2h`(贴着 ingest 下限)/ `2–6h` / `6–24h` / `1–3d` / `>3d`
- actionable_latency:`≤30m` / `30m–2h` / `2h–6.5h`(当个交易时段内)/ `隔一交易日` / `更晚`

### 3.3 每桶报告指标

| 指标 | 说明 |
|---|---|
| n / graded_n | 桶内笔数 / 扣除死区(is_hit=NULL)后的判定笔数——**死区排除与上游一致,分母必须用 graded_n** |
| hit_rate + Wilson 95% 下界 | 方法论复用上游(诚实榜用 Wilson 下界),但只评**我们跟到的子集**,绝不反推改写博主 tier |
| S 账 abnormal(mean/median)| 桶内信号质量 |
| E 账 actual_abnormal(mean/median)| 桶内实际拿到的 |
| entry_diff_bps(p25/p50/p75)| 延迟→入场价劣化的直接证据 |

**目的**:画出"edge 随延迟衰减"曲线——若 `≤2h` 桶的 S 账 abnormal 显著高于 `1–3d` 桶,说明时效是 alpha 的一部分,慢=自愿放弃。**诚实声明:观察性结论,非因果**(桶之间 handle/ticker/时段构成不同);n<5 的桶只报 n 不报率值(echo 上游 MIN_CALLS_FOR_SCORE)。

## 4. 聚合维度与样本纪律

- 维度:handle × tier(收录时快照 `signals.tier`)× rule_version × 月份。任何单元格 graded_n<5 → 显示 n、不显示率。
- **覆盖率与选择偏差(必报,防"只统计跟了的")**:
  - eligible_n(水位内 21d PROVEN bullish 喊单总数)vs followed_n;`decision='skipped'` 按原因码分布全量列出(ORDER_LEDGER_SPEC §5.1 字典)。
  - 我们跟单子集的 S 账 hit_rate vs 同期该 handle 全量 call_outcomes hit_rate 并排展示:若我们"跟到的"显著差于"他喊的",说明我们的过滤/时延在**负选择**(例如 `signal_stale` skip 掉的恰是最快最好的)。
- 与诚实榜的关系:tier 永远以**收录当日 CSV 快照**为准(`signals.tier` + `tier_csv_date`),事后博主降级不改历史行——决策当时知道什么就按什么评。

## 5. 可复现与诚实条款

1. 每期报告头部必含:rule_version 集合、对账代码/报告代码 commit、watermark 区间(`ingest_watermark`)、tier_csv_date 范围、生成命令一行(`uv run ...`)。
2. 窗口固定为"全历史 + 最近月切片",不得另开任意窗口挑数字;新切片维度=升报告版本。
3. 已知误差源(每期报告附录原样列出,直到消除):S 账价格序列复权方式未核实(stock-picker prices.db 内部口径)、E 账盘中成交 vs 收盘基准锚差、模拟盘费用/股息机制未实测、`tweet_blocked=TRUE` 的帖子文本对外展示禁用(统计照常计入)。
4. 绩效报告是**观察记录,不是策略验证**:转向后验证管线/门禁不复存在,本口径不产生"PASS/上线"判定;若未来任何真金动议(红线 2:未经 operator 逐项授权一律禁止),绩效报告不可替代重建的准入验证。
5. 含任何绩效结论的报告发出前过 %Audit(最高准则)。

## 6. 数据流与产物

```text
只读:ledger.duckdb(signals/orders/fills/positions_daily + 视图)
只读:~/.stock-picker-mcp/trackrecord.db(call_outcomes)、诚实榜 CSV(strip \r!)
只读:SPY/个股收盘序列(抽查复算用;首选 ~/.stock-picker-mcp/prices.db 同源,降低基准漂移)
  ↓ 批任务(%Valid 拥有,日/周跑,纯读)
产物:reports/follow_perf/<run_date>/
  ├─ follow_perf.parquet      (逐笔三套账+归因+延迟桶,机器可读)
  ├─ FOLLOW_PERF_REPORT.md    (人读:三账并排、延迟衰减表、覆盖率、exit_reason 归因)
  └─ run_meta.json            (§5.1 复现信息)
```

- **不写 ledger**(单写者是 %Exec;本任务纯读、产物进 reports/,可随时全量重算——派生数据不是 authoritative store)。
- %Dash 消费 parquet 出监控页;报告 MD 经 %Audit 后由 Lead 决定是否入库。

## 7. 开放问题(待上游/会签解决)

1. 平仓腿成交粒度依赖 C6 页面能力(逐笔 or 聚合)→ E 账 avg_fill 的精度受限,先按聚合设计。
2. `hold_21d` 从我们入场日起算(本文 §2.1)vs 从 S 账 entry_date 起算——两者差 = 我们的入场延迟;v0 选前者(执行语义自洽),归因里 window_diff 吸收差异。**需 Lead 确认。**
3. 抽查复算的数据源:首选只读 prices.db(与上游同源);若复用受限改 yfinance,需在报告标注"复算源≠上游源"。
4. direction_flip 退出触发的信号(`decision='skipped', reason='exit_trigger'`)本身不进绩效池(它不是开仓),但其触发的平仓效果计入 early_exit_diff——确认无异议。
