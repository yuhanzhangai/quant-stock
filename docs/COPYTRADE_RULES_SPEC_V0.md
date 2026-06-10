# 跟单决策规则 Spec v0(MVP)— PROVEN 博主 bullish 跟单

> 作者:Strat(quant:1.1)· 2026-06-10 · r2(r1=3 视角×25 agent 对抗自审修复 14 项含 1 blocker;r2=Lead 三项裁决落入)· 状态:**r2 定稿候选,未经独立复核**(operator 2026-06-10 废止强制审核制度,按 CLAUDE.md 质量纪律自检后 commit;曾按旧制送审 %Audit,未开审即废止)· 上线仍阻塞于 §6 确认项
> 数据依据:INTEGRATION_NOTES.md + 对 `trackrecord.db` / `leaderboard_honest_2026-06-10.csv` 的只读实测(附录)。
> 范围:只定**决策规则**;浏览器执行细节归 Exec,回放/验证归 Valid。纯模拟盘,无真金。

## 0. 决策节奏与信号读取
- **每交易日一次决策循环:15:30 ET**(半日市提前至收盘前 30 分钟),先处理退出、再入场,指令单交 Exec 收盘前执行。
- **信号读取(防漏防重)**:查询 `analyst_calls` 用 `call_ts > 水位 − 7 天` 安全回看 + **已处理 tweet_id 集合去重**(入库乱序实测严重,严格 `>` 水位会永久漏单);指令单以 tweet_id 幂等判重,水位与指令单**同事务落盘**(崩溃重放安全)。
- **入场时点对标(关键口径)**:call_outcomes 的 entry_close = **喊单日之后第一个交易日的收盘价(T+1 close)**,实测 16113/16113 行 entry_date > call_date,0 行当日。故每个信号的**基准入场日 T_entry = call_date 后第一个交易日**;信号仅在 T_entry 及 T_entry+1(重试日)有效,之后作废落日志(取代 wall-clock 作废,周末/假日喊单自然顺延到周一,无系统性丢单)。
- **PROVEN 名单**:取目录中最新日期 `leaderboard_honest_<date>.csv`(每日 13:30 PT 刷新,决策时实际为 T-1 文件,接受);⚠️ CRLF,`status` 必须 strip。**最新文件早于今天 − 2 个交易日或目录为空 → 当日冻结新入场(退出照常跑)并告警。**

## 1. 入场
**信号定义**:`is_call=1 AND direction='bullish'` AND handle ∈ {CSV `horizon=='21d'` 且 strip 后 `status=='PROVEN'`}。**白名单制:非 PROVEN 一律不用**(含 TRACKING / INSUFFICIENT / PROVEN_1REGIME / PROVEN_BAD 及其 1REGIME 变体;FADE 是 PROVEN_BAD 的别名,非 CSV 实际值)。
**过滤管线(顺序执行,每步 skip 均落日志含原因码)**:
1. **去重**:同 `handle×ticker×direction` 保留 `call_ts` 最新一条。
2. **一票一仓**:已持仓或有未完结入场挂单的 ticker → skip(只记 provenance,provenance 不参与退出判定)。
3. **多空冲突**:`[决策时点 − 7×24h, 决策时点]`(含当日)内任一 PROVEN handle 对同 ticker 有 `is_call=1 AND direction='bearish'` → skip(实测约 3% 信号受影响)。**冲突与翻转检查均为对 analyst_calls 的独立窗口直查,不受水位/信号有效期约束。**
4. **可交易性**:现价 ≥ $3;ticker 在我方 prices.db 有日线;`floor(每单金额/现价) ≥ 1` 且实际敞口 ≥ 目标金额 80%(原因码 zero_share / granularity)。
5. **同票合并**:按下述优先级排序后,同 ticker 只保留最高优先级一条;**入场归因 handle = 被采纳信号的 handle(唯一)**。
**优先级**(槽位不足时择优,保证确定性):`wilson_lo` 降序 → `conviction`(high>medium>low>None)→ `confidence` 降序 → handle 字典序 → ticker 字典序。
**槽位会计**:可用槽 = N − 当前持仓数 − 未完结入场挂单数;**当日下达的退出指令不腾当日槽**(次日生效,保证任何时刻实际持仓 ≤ N)。
**入场执行**:T_entry 收盘前市价(严格对标 entry_close);当日失败 → T_entry+1 收盘前重试一次,**重试前重跑全部过滤管线**,且执行价较 T_entry 收盘价上漂 > 5% 即作废;再失败作废。重试挂单持续占槽。

## 2. 仓位(MVP 等额)
| 参数 | 建议值 | 理由 |
|---|---|---|
| 每单金额 | **$5,000 固定**(整数股向下取整) | = 5% 初始权益($100k 模拟盘假设,**待 Exec 确认**,非 $100k 则按 5% 等比缩放);单仓止损 -8% ≈ -0.4% 权益 |
| 最大持仓数 N | **10** | 满仓占用 ≤50%,留 50% 现金缓冲;全部止损同触 = -4% 权益;10 仓×21 交易日 ≈ 0.5 空槽/天,匹配择优逻辑 |
| 单 handle 上限 | **5 仓(=N/2)**,在槽位分配阶段逐条检查,超限 skip(原因码 handle_cap) | 活跃 PROVEN 仅 3 人且流量极不均(shay 178 单/30d vs joely 15)。**Lead 已批准(2026-06-10):作为 rule_version 内可调参数记录,模拟盘跑 2-4 周后用真实数据复议** |
| 杠杆/做空/加仓 | 无 | KISS;现金多头 only |

## 3. 退出(自建;stock-picker 无退出信号 — INTEGRATION_NOTES §4)
每日循环按序判定,先到先触发:
| 优先 | 规则 | MVP 默认 | 理由 |
|---|---|---|---|
| 1 | **止损** | 决策时点现价 ≤ 入场成交价 × **0.92(-8%)** → 当日卖出 | 经典 7-8% 纪律;日检+跳空可能击穿 8%,纸面阶段实测该滑点 |
| 2 | **同博主翻转** | 归因 handle 对同 ticker 出现 `is_call=1 AND direction='bearish'` 且 `call_ts` > 入场信号 call_ts → 卖出 | 博主止盈/离场被归类为 bearish;实测持有窗内 ≥11.1% 触发(右截尾,系下限) |
| 3 | **到期** | **实际成交日(不含当日)起第 21 个交易日** → 卖出 | 对标 call_outcomes(实测 21d = 21 交易日,≈29-35 自然日、中位 30) |
**退出执行规则(blocker 修复)**:卖出失败(停牌/LULD/自动化故障)→ 该仓标记 frozen(仍占槽,停牌期间止损检查跳过),此后每个循环自动重发直至成交,全程落日志;退市/转 OTC → 以最后可得价格强制平仓记账并报 operator 复核;frozen 持仓在对账报表单列。
其他 handle 的 bearish、tier 中途掉档:**不触发退出**,只停新开。`neutral` 全程忽略。

## 4. FADE 反指
**MVP 完全忽略,不反向跟单**(Lead 拍板,KISS)。反指引入做空复杂度且其"反向 edge"未经我们口径验证;v1 若做,先离线回放。

## 5. 诚实点(评审重点;本节及附录数字未经独立复核,口径与复现路径见附录)
1. **edge 未经验证**:21d PROVEN 共 5 人,榜首两位已沉默(etfswingtrader 末单 2023-06、danzanger 2024-07);**活跃源仅 3 人**,wilson_lo = 0.591 / 0.519 / 0.503,后两位勉强过 0.5。模拟盘阶段**就是验证本身**。
2. **hit ≠ 赚钱**:is_hit 为**方向调整的对 SPY 超额命中**(bullish = abnormal_return>0;bearish 取反;|超额|≤0.5% 为 NULL 死区不计入)。本 spec 只跟 bullish,对我们 hit = 跑赢 SPY;**绝对盈亏还吃市场 beta,MVP 不对冲**。榜单 hit_rate 含 bearish 样本,与我们子集不完全同口径。
3. **容量错配 → 选择效应**:去重后中位 ~12 信号/天 vs ~0.5 空槽/天,采纳率 ~4%;我们交易的是优先级选出的子集,**表现 ≠ 榜单全样本**;靠确定性规则 + skip 全量日志保持可审计可复现。
4. **T+1 入场的含义**:按基准日收盘入场 = 放弃喊单至次日收盘间的动量,但**那正是榜单度量的东西**——这是诚实复制,不是缺陷。注意 call_outcomes 有 ~11% 长尾(entry 滞后 ≥5 天,系数据回填),离线回放需剔除或单列。
5. tier 信息滞后一天(T-1 CSV);call_date 为 UTC 日期口径,与 ET 偶有跨日差,对标以 call_outcomes 自身口径为准。

## 6. 待确认(不阻塞评审,阻塞上线)
- [x] ~~Lead 拍板:单 handle 上限~~ **已批准**(2026-06-10,见 §2;T_entry 对齐口径同日批准)
- [ ] Exec:模拟盘初始资金;收盘前执行窗口峰值 ~5 单可行性;停牌/退市场景的人工兜底流程
- [ ] Valid:用 call_outcomes 做规则离线回放(**入场必须按 T_entry 收盘建模**,剔除回填长尾;Lead 已派)。⚠️ 回放报告须显式声明 **point-in-time 局限**:用今日 PROVEN 名单回放历史含幸存者偏差,结论只用于"跟这 3 人是否 sane"的判断,**不是 edge 证明**
- v1 候选(MVP 不做):止损后冷却(需先实测旋转门频率)、其他 handle bearish 触发退出、tier 掉档强制退出、conviction/confidence 门槛

## 附录:数据实测(2026-06-10,trackrecord.db 只读 + 当日 CSV,均可复现)
- 21d tier 分布:PROVEN 5 / TRACKING 49 / INSUFFICIENT 85 / PROVEN_BAD 4 / PROVEN_BAD_1REGIME 6
- 近 30d PROVEN bullish 流量:shay 178 单(62 票)、jimmyhuli 107 单(47 票)、joely 15 单(7 票);近 14d 去重信号中位 12/天、峰值 27/天
- 冲突率(口径:is_call=1 双向,7 自然日回看):9/300 = 3%
- 同博主翻空率(口径:is_call=1,30 自然日窗,60d 样本,右截尾):52/469 ≈ 11.1%(下限)
- 入场基准:horizon=21d 共 16113 行,entry_date > call_date 16113 行(100%),gap 1 交易日为主(~89%),≥5 天长尾为回填
- 21d = 21 交易日(全量分布:自然日 29-35,中位/众数 30)
- 入库乱序:5919/5963 行存在 call_ts 倒挂(max 907h)→ 必须安全回看 + tweet_id 去重
