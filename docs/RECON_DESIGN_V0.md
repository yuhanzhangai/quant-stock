# RECON_DESIGN_V0 — Firstrade 页面读数 vs 本地 Ledger 对账设计(预研 v0)

> **状态:预研设计稿(spec-only,不含代码),%Valid 出品,待 %Audit 审 + Lead/%Exec 会签。**
> **从属关系:`docs/ORDER_LEDGER_SPEC.md` 是 ledger 真源,本文是其 §7(对账)的展开;两者冲突时以 ORDER_LEDGER_SPEC 为准,本文提案性扩展(§5 留档表、§8 反馈)需被该 spec 采纳后方生效。**
> 事实源:ORDER_LEDGER_SPEC.md(表/视图定义)· INTEGRATION_NOTES.md · Exec worktree `track/exec`@49bebfb 的 reader/models/selectors 现状(2026-06-10 只读侦察)· scripts/paper_runner.py + src/backtest/paper_session.py(v2.5A 留档经验)。

## 0. 目标与范围

对账回答一个审计问题:**"本地 ledger 记的,和 Firstrade 模拟盘页面显示的,是同一个世界吗?"** 不一致即停新单(红线 6:出错先停),差异本身是审计证据,绝不 UPDATE 抹平(ORDER_LEDGER_SPEC §5.3/§7)。

两个锚点(Lead 指定):
1. **positions_daily 快照核对**:fills 累计推算持仓 vs 券商页面持仓(B 组检查)。
2. **fills↔orders 完整性**:ledger 内部自洽(A 组检查)——这组不依赖页面可读性,落地零阻塞。

v2.5A 的教训直接吸收:旧 `fills` 表(无 size、无关联 ID、bps 硬编码)从未被任何分析消费过——**没有数量和关联的成交记录是死数据**。本设计所有检查都建立在 ORDER_LEDGER_SPEC 的 qty + 关联 ID 之上。

## 1. 对账两端的数据面

### 1.1 本地端(ledger.duckdb,ORDER_LEDGER_SPEC §4)
直接消费其视图,不另建口径:`v_orders_current`(订单现状)· `v_fills_effective`(剔除作废对的有效成交)· `v_order_filled`(每单成交聚合)· `v_recon_ledger_qty`(fills 累计推算净持仓)· `v_positions_eod`(每日每票最新快照)· `v_pdt_latest`(簿记快照)。

### 1.2 Firstrade 页面端(Exec worktree 现状,2026-06-10)
| 数据 | 现状 | 对账可用性 |
|---|---|---|
| 持仓表(symbol/qty/avg_price/market_value) | reader 代码已写,**选择器全部 verified:false,未实跑** | B1/B2 的输入;列序是假设 |
| 现金/购买力 | 同上 | B3 输入 |
| 模拟盘环境标志 / 登录态标志 | 同上 | 对账前置条件(P0) |
| 提交后确认文案 | trader 提交后读一次 | fills 的 raw_text 来源之一 |
| **订单状态页 / 逐笔成交历史** | **零代码、零选择器,页面是否存在/什么粒度未知** | B4 整组标为假设,v1+ |
| unrealized_pnl / 当日 close / 账户总值 total_value | 模型留位但 reader 不填,页面是否暴露未知 | positions_daily 这三列 v0 允许 NULL |

**设计原则:页面端一切皆假设,直到 C5 选择器实盘核验。** 本设计按"可降级"组织:A 组(纯 ledger)永远可跑;B 组按页面能力逐项点亮。

## 2. 不变量清单(检查项字典,封闭集,扩充须升版)

判级:**HALT**(违反 = 停新单 + 告警 operator)/ **WARN**(记录 + 日报,不停单)/ **INFO**。

### A 组 — ledger 内部完整性(fills↔orders),每日必跑,不依赖页面

| ID | 不变量 | 判级 |
|---|---|---|
| A1 | 每条 `v_fills_effective.order_id` 在 `orders` 中存在(孤儿成交 = 漏记订单或回采串单) | HALT |
| A2 | 每单 `v_order_filled.filled_qty ≤ qty`(超额成交 = 重复回采或重复下单) | HALT |
| A3 | 状态↔成交一致:`filled` ⟺ filled_qty = qty;`partial` ⟺ 0 < filled_qty < qty;`rejected` ⟹ 无有效 fill;`cancelled` 且有 fill ⟹ 事件流中必有 `partial` 历史 | HALT |
| A4 | `submitted` 订单出现 fill 但状态未推进 —— 允许一个回采周期的时滞,超过(默认 1 个交易日)未推进才触发 | WARN→HALT |
| A5 | 事件流完整:每个 order_id 的 seq 从 0 连续无缺口;终态(filled/cancelled/rejected)后无后续状态行(writer 已拦,对账作为独立复核——**writer 自检不能当对账用,两套账互验**,同 ORDER_LEDGER_SPEC §4.5 簿记互验原则) | HALT |
| A6 | 关联链完整:每单 `signal_id` 存在;开仓单(side=buy)其 signal `decision='followed'`;平仓单(side=sell)`exit_reason` 非空,且 `direction_flip` 时 `exit_trigger_signal_id` 非空 | HALT |
| A7 | 时间线单调:`signals.call_ts ≤ ingested_ts ≤ orders.submitted_ts`;`fill_ts ≥ submitted_ts`(容差 ±5min,fill_ts 来自页面展示时区解析,易错) | WARN |
| A8 | 卖不超持:任意时点累计卖出 ≤ 累计买入(按 fill_ts 排序逐票重放;v1 只做多,出现负持仓 = 记账错) | HALT |
| A9 | 水位新鲜度:`max(ingest_watermark.poll_ts)` 距今 ≤ 阈值(默认 2× 轮询间隔);异常说明采集断了——**没有新信号 ≠ 系统健康** | WARN |

### B 组 — ledger vs Firstrade 页面(positions_daily 为锚)

| ID | 不变量 | 判级 |
|---|---|---|
| B0 | 前置:快照抓取时 `paper_account_marker` 可见且登录态有效;否则当日对账记 `aborted`(不产生假 PASS) | HALT(对账中止)|
| B1 | **逐票数量**:`v_recon_ledger_qty` FULL OUTER JOIN `v_positions_eod`(缺行按 0),`ledger_qty = qty` 须逐票成立。**broker 有票 ledger 没有**(missing_local:漏采成交/agent 重复下单)与 **ledger 有票 broker 没有**(missing_broker:成交未发生/页面解析丢行——reader 现在脏行静默跳过,正是此风险)都算不一致 | HALT |
| B2 | 均价成本:ledger 推算加权成本 vs `positions_daily.avg_cost`,容差内(§3)。券商均价算法(是否含费、部分成交舍入)未知,先 WARN 收集分布再定阈值 | WARN |
| B3 | 现金:页面 cash vs `v_pdt_latest.settled_cash`。**语义已知不等**(页面 cash 可能含未结算;模拟盘股息/利息机制未知),v0 只记录差值序列供观察,不判错 | INFO |
| B4 | 订单状态页 vs `v_orders_current`、成交历史页 vs `v_fills_effective`(逐单状态/逐笔成交直接核对)| v1+,**全部前提未验证**:页面是否存在、何种粒度。若 Firstrade 提供,这是比 B1 更强的对账面;若只有聚合,降级为日终成交汇总核对 |

### 检查间依赖
B1 依赖 A 组先 PASS:ledger 内部不自洽时,`v_recon_ledger_qty` 本身不可信,B1 的"一致"可能是双错抵消。执行顺序:A 组 → B0 → B1/B2/B3。

## 3. 容差与判定

| 量 | 容差 | 理由 |
|---|---|---|
| qty | **0(精确相等)** | v1 整股、无碎股(下单引擎只下整数股);DECIMAL 字段是 schema 前瞻,不是容差许可 |
| avg_cost | \|diff\| ≤ max($0.01, 5 bps) → PASS;超出 WARN | 页面展示舍入(2 位小数)+ 算法未知;**阈值是初始拍的,跑两周收集分布后由数据定,调整须留档** |
| cash | 不判,记录差值 | 见 B3,语义缺口未补前判定无意义 |
| fill_ts | ±5 min | 页面时间粒度/时区解析风险 |

## 4. 流程与时序(每美东交易日)

```text
盘后(建议 16:30 ET 后,给成交页/持仓页结算显示留时间;具体时点待 C5 实测页面更新延迟)
  1. [%Exec] 抓持仓页(+现金)→ 追加 positions_daily(含 raw_text 原件)
  2. [%Exec 进程内,逻辑归 %Valid] 跑 A 组 → B0 → B1/B2/B3
  3. 全 PASS → pdt_ledger 落 eod_snapshot(note: recon=ok)          (ORDER_LEDGER_SPEC §7.3)
  4. 任一 HALT → 创建 data/execution/RECON_HOLD 文件 + 告警 operator;
     引擎下单前检查:KILL(kill-switch,operator 管)与 RECON_HOLD(对账闸,排查后人工移除)
     二者独立——对账失败不该烧掉 kill-switch 的语义                  ← 提案,待 %Exec 会签
  5. 修复走 §5.3 修正机制(追加 voids 行/修正事件),重跑对账,PASS 后移除 RECON_HOLD
```

盘中对账(下单后即时核对确认文案 vs 订单参数)属 %Exec trader 的职责边界,本设计不重复;本设计管**日终账实核对**。

## 5. 对账结果留档(提案:新增两表,纳入 ORDER_LEDGER_SPEC v1.1)

对账本身是审计行为,其结果必须同样 append-only 留档(形状仿研究库 `validation_results` 的 gate 模式):

```sql
CREATE TABLE IF NOT EXISTS recon_runs (
    run_id        TEXT PRIMARY KEY,        -- 'rec_' + ULID
    run_ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    trade_date    DATE NOT NULL,           -- 对账针对的美东交易日
    scope         TEXT NOT NULL,           -- 'eod_full' | 'ledger_only'(页面不可用时的降级)
    result        TEXT NOT NULL CHECK (result IN ('pass','warn','halt','aborted')),
    checks_run    INTEGER NOT NULL,
    checks_failed INTEGER NOT NULL,
    code_commit   TEXT NOT NULL,           -- 对账代码版本(可复现)
    note          TEXT
);
CREATE TABLE IF NOT EXISTS recon_findings (
    finding_id  TEXT PRIMARY KEY,          -- 'fnd_' + ULID
    run_id      TEXT NOT NULL,             -- 逻辑 FK → recon_runs
    check_id    TEXT NOT NULL,             -- 'A1'...'B4'(§2 字典)
    severity    TEXT NOT NULL CHECK (severity IN ('halt','warn','info')),
    ticker      TEXT,                      -- 涉票(全局检查可空)
    order_id    TEXT,                      -- 涉单(可空)
    expected    TEXT,                      -- ledger 侧值(序列化)
    actual      TEXT,                      -- 页面侧值(序列化)
    details     TEXT,                      -- JSON:diff、涉及行 ID、原始文本片段
    resolved_by TEXT                       -- 修正后回填?否——append-only,修正行在 fills/orders,
                                           -- 此处只追加新 finding(status='resolved')关联旧 finding_id
);
```

留档纪律与主表一致:单写者(%Exec 进程)、不 UPDATE、每日 parquet 备份。`recon=ok` 写入 pdt_ledger eod_snapshot 的现行设计(§7.3)保留,两处互验。

## 6. 职责切分(待会签)

- **%Valid(我)**:检查项字典(§2)、容差(§3)、判级语义的 owner;对账逻辑的 pytest(用内存 DuckDB 构造每个不变量的违反样例,验证判级正确)。
- **%Exec**:对账 runner 的宿主(单写者约束:recon 结果写入必须经它的 writer;对账逻辑作为库函数被其 EOD 循环调用);页面抓取与 raw_text 留档。
- **%Dash**:消费 `recon_runs`/`recon_findings` 只读展示(连续 N 日 pass 的绿灯、HALT 即红)。
- **%Audit**:本设计审核;以及定期(周)抽查 raw_text 原件 vs 结构化行的解析正确性——解析错误是 B1 假阳/假阴的最大来源。

## 7. 已知未知(实施前必须消除或显式接受的假设)

1. 所有 CSS 选择器 verified:false(唯一真实 URL 是 login);持仓表列序(col0=symbol…)是猜测。→ C5 实盘核验解锁。
2. 订单状态页/成交历史页是否存在、粒度(逐笔 or 聚合)未知 → B4 与 `fills` 表粒度均是假设(ORDER_LEDGER_SPEC §9 已自我标注,%Exec 会签项)。
3. 持仓页是否暴露 unrealized_pnl/close/avg_cost 精度未知 → B2 可能降级不可用。
4. 模拟盘现金机制(股息、利息、初始资金重置)未知 → B3 只观察。
5. 登录态有效期/2FA 重弹频率未知 → 影响"每日全自动 EOD 对账"成立与否;若需人工介入,对账改为"有快照才跑,缺日留空+WARN",**不得拿陈旧快照对今日账**。
6. reader 对脏行静默跳过 → 对 B1 是系统性风险(丢行=假 missing_broker),建议 %Exec 改为"解析失败行计数并入 raw_text 留档",对账拿到行数核对(页面行数 vs 解析行数)。

## 8. 对 ORDER_LEDGER_SPEC 的反馈(发现于本设计推演,报 Lead/spec owner)

1. **signal_id 撞键风险(高优)**:`signal_id = 'sig_' || tweet_id` 且 `tweet_id UNIQUE`,但上游 `analyst_calls` 主键是 **(tweet_id, ticker)** ——同一条帖多个 ticker(pair call,INTEGRATION_NOTES §2 明确存在,如"long $NVDA short $INTC")会产生同 tweet_id 多条喊单;v1 只跟 bullish 也挡不住同帖双 bullish ticker。现 DDL 下第二条插入即撞 PK,**整票信号被静默丢弃或报错**。建议:`signal_id = 'sig_' || tweet_id || '_' || ticker`,UNIQUE 改 (tweet_id, ticker)。
2. **B1 需要"零持仓也可证"**:`v_recon_ledger_qty` 只对有成交的票产出行;全平的票(累计=0)与从未交易的票在视图中无法区分页面侧静默丢行。建议对账实现用"ledger 历史出现过的 ticker ∪ 页面 ticker"全集做 FULL OUTER JOIN(本设计 B1 已按此写)。
3. `recon_runs`/`recon_findings` 两表提案(§5)请纳入 spec v1.1,保持单一 DDL 真源。
