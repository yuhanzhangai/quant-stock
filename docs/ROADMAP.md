# ROADMAP — 博主跟单 + 下单留档(2026-06-10 转向后路线)

> 取代 `docs/MIGRATION_PLAN.md`(crypto→stock 研究路线,已随转向废止存档)。
> 纪律沿用:**一次一个 phase · 有产出物 · 可复现 · 不达标不进下一步。**
> 方向:stock-picker 诚实榜 PROVEN 喊单 = 信号源 → 规则引擎 → Firstrade 模拟盘自动下单 → **每单留档**(订单全记录 + 下单依据原帖快照)→ 对账 + 绩效跟踪。
> **诚实基线**(Valid 离线回放,PIT 局限已声明):活跃 PROVEN 仅 3-5 人,hit 60.4%/Wilson 下界 0.507——edge 薄,**模拟盘前向实测是唯一验证,本项目不预设盈利**。

## P0 设计与原型 ✅(2026-06-10 完成)
- 信号适配层三原型(Data,track/data):诚实榜读取/增量轮询(延迟实测 p50≈45min)/原帖快照
- 跟单规则 spec v0→r2(Strat,track/strat):21d PROVEN bullish / 等额仓位 N=10 / 单 handle≤5 / 止损-8% / 持有 21 交易日 / 翻空提前退
- ORDER_LEDGER_SPEC r2 + Exec/Dash 双会签(r3 修订队列:Exec 4+3、Dash 3、金额配置化、读写分离 parquet 导出)
- 对账设计 + 三套账绩效口径 + 离线回放 sane check(Valid)
- 执行层安全底座(Exec,track/exec):PAPER_ONLY 硬钉 / kill-switch / 真人节奏 / 选择器未核验拒跑

## P1 实施闭环(纸上→代码)
- [ ] ORDER_LEDGER_SPEC **r3 定稿**(Lead 出稿,Exec/Dash 复核)→ `src/execution/ledger/` DDL+写入层实施(Exec)
- [ ] 最小信号管线(Data):PROVEN@21d × bullish 水位轮询 → 去重/冲突过滤 → signals 表候选;双类股 issuer 归并 v0.1(Strat 补丁)
- [ ] 规则引擎(Strat spec → 代码):signal → followed/skipped 决策 + PDT/结算软约束
- [ ] 合分支:track/data + track/strat + track/exec 依次并 main
- **通过标准**:模拟信号端到端跑通(假 fill),ledger 五问可答,测试覆盖写入幂等/append-only

## P2 Firstrade 模拟盘接通(读)
- [ ] **operator 人工首登**(`make exec-login`)留登录态 + 核验全部选择器(paper_account_marker 最关键)
- [ ] 读层稳定:持仓/账户/行情回采 + agent_runs 心跳
- **通过标准**:连续 3 个交易日只读轮询无封禁/无选择器漂移

## P3 跟单闭环(写,全自动模拟盘)
- [ ] 下单执行(dry_run→真点击,PAPER_ONLY 断言)+ fills 回采对账 + 每循环 parquet 导出
- [ ] 退出引擎:止损/21d 到期/翻空触发
- **通过标准**:首批 ≥5 笔真实喊单跟单全链留档,Valid 对账 9 项不变量全过

## P4 监控与常态运行
- [ ] Dash 两页转正(模拟盘监控/订单留档查看,读 parquet)+ 日报(Telegram)
- [ ] Medic 守护全功能(auto-Enter 修复后重启用)+ 绩效周报(S账 vs E账,延迟分桶)
- **通过标准**:operator 每天只看 dashboard/Telegram 即可掌握全部状态;连续 2 周无人工干预运行

## 红线(不变)
PAPER_ONLY,真金交易未经 operator 逐项授权一律禁止 · stock-picker 库只读 · 密钥/登录态不入库 · 单账号人类节奏出错先停 · 归档不复活 · 绩效报告诚实(PIT/延迟成本必须可见)
