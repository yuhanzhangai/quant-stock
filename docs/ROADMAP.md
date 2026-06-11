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

## P1 实施闭环(纸上→代码)✅(2026-06-11 关门:端到端演练四问 PASS + 独立对账验收 9/9 PASS)
- [x] ORDER_LEDGER_SPEC **r3 定稿**(Lead 出稿,Exec/Dash 复核)→ `src/execution/ledger/` DDL+写入层实施(Exec)
- [x] 最小信号管线(Data):PROVEN@21d × bullish 水位轮询 → 去重/冲突过滤 → signals 表候选;双类股 issuer 归并 v0.1(Strat 补丁)
- [x] 规则引擎(Strat spec → 代码):signal → followed/skipped 决策 + PDT/结算软约束
- [x] 合分支:track/data + track/strat + track/exec 依次并 main
- **通过标准**:模拟信号端到端跑通(假 fill),ledger 五问可答,测试覆盖写入幂等/append-only

> ⚠️ **重大方向修正(2026-06-11,Data 调研 docs/FIRSTRADE_API_RESEARCH.md 实证)**:
> **Firstrade 根本没有 paper trading / 模拟盘产品**——项目原名"Firstrade 模拟盘"基于错误假设。
> firstrade 非官方库每个下单接口都打**真实账户**(真金)。浏览器路线登录卡 2192。
> operator 拍板:**先走本地模拟盘(按真实市场价模拟成交,零真金、零券商),验证 edge;**
> **真钱 $300 路线已 operator 授权但停泊**——edge 跑出来再上(接库基础设施已备齐)。

## P2 本地模拟盘前向测试(取代原 Firstrade 接通)
- [ ] **PaperBroker**(`src/execution/paper_broker.py`):消费规则引擎 followed 决策 → 按**真实市场价**(Data 价源:yfinance/prices.db,信号时点价)模拟成交 + 可配滑点 → 写 orders/fills/positions_daily(复用现有 ledger writer,演练已验全链)
- [ ] 退出引擎前向运行:21d 到期 / 止损 / 翻空,按真实日收盘价每日评估(演练时因无价格路径未覆盖,本地前向跑可覆盖)
- [ ] 定时前向运行(每交易日)+ 每轮 parquet 导出 → Dash 自动脱 MOCK
- **通过标准**:连续 ≥2 周真实喊单本地跟单,全链留档,Valid 对账 9 项不变量 + S账/E账(本地成交即 E账)/S−E 归因全过

## P3 真钱实盘(已授权,停泊;edge 验证后启用)
- [ ] 仅当 P2 跑出可信 edge 后启用;接 firstrade 库打真实账户
- [ ] **硬护栏**:账户号白名单 fail-closed(只打指定账户)+ 总额硬顶 $300 + 单笔 $100/最多 3 仓(operator 2026-06-11 定)+ kill-switch + **第一笔人工盯单确认**
- **通过标准**:首笔人工确认成功 + 小额观察数笔 + operator 明确放开全自动

## P4 监控与常态运行
- [ ] Dash 两页(已上线 MOCK,P2 真数据自动切)+ 日报(Telegram)
- [ ] Medic 守护全功能 + 绩效周报(S账 vs E账,延迟分桶)
- **通过标准**:operator 每天只看 dashboard/Telegram 即可掌握全部状态;连续 2 周稳定运行

## 红线(修正)
- 真金交易未经 operator 逐项授权一律禁止;**P3 真钱已授权但停泊**(总额硬顶 $300、单笔 $100、白名单 fail-closed、首单盯单),**edge 验证前不启用**
- 本地模拟盘默认无真金 · stock-picker 库只读 · 密钥/登录态不入库 · 出错先停 · 归档不复活 · 绩效报告诚实(PIT/延迟成本必须可见)
