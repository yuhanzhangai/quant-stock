# MIGRATION_MANIFEST — 全仓资产分流清单(crypto→stock)

> 2026-06-10 由 Lead 基于 6 路并行盘点(src 核心 / 策略库依赖图 / 54 脚本 / 测试实跑 / 配置密钥 / 文档面板)制定。
> 动作语义:**keep**=原样复用 · **frozen**=只读存证一字节不改 · **C1/C2/C3**=对应 checkpoint 替换 · **archived**=已移入 archive/ 或 docs/legacy/(只移动零修改,git mv 保留历史)。
> 测试基线(整理前后必须一致):`uv run pytest -q` → **67 collected,58 passed / 9 skipped(replay 组缺本地 ETH 数据,非代码问题)/ 0 failed**。

## 一、本次整理(2026-06-10)已执行的动作

| 动作 | 对象 | 依据 |
|---|---|---|
| 归档 → `docs/legacy/` | 根目录 22 个 QuantLab 时代 md(MASTER_ROADMAP、PROJECT_STATUS、WEEK1_PLAN、SHORT_STRATEGY_REPORT、修改方向 + 17 个 v2.x spec/closeout) | 互引闭合在簇内,零活链接断裂;quant-stock 现状以 team/PROGRESS_LOG.md 为准 |
| 归档 → `docs/legacy/files/` | files/ 下 3 份 QuantLab 创世文档旧拷贝 | 零依赖,且 files/CLAUDE.md 有被误读为现行宪法的风险 |
| 归档 → `archive/scripts/` | 39 个 crypto 运行时/一次性研究脚本(live_signal×3、paper_trader 系、tournament×2、short 系×10、tsla OKX 系×3、grid_search 系、报告生成系等) | 零代码依赖(测试只 import paper_runner/top50_paper_monitor,均保留;Makefile 只引 verify_okx,保留);其中 minswing_grid_search/validate_3seg/generate_58h_report 属 v3 参数溯源,**只移动一字节未改** |
| 归档 → `archive/strategies/{long,meta}/` | long/ 41 文件 + meta/ 6 文件 | 全仓无任何 `strategies.long.*`/`strategies.meta.*` import;frozen 链活引用全部锚定 root 文件;registry 路径字段已同步,状态字段未动。**修正(对抗自查发现)**:4 个现役验证管线脚本(out_of_sample_test/walk_forward/event_backtest/run_backtest)import 的扁平策略模块(`src.strategies.aggressive_momentum` 等)早在 fork 前 commit `3500d92` 即被删除——这 4 个脚本在 HEAD 就不可直接运行,断裂非本次移动引入,C2 重建数据喂入时一并修复 import |
| 停用 → `dashboard/legacy_pages/` | 2_资金费监控.py | funding rate 无美股对应物;零依赖;Lead 已拍板停用归档 |
| 取消跟踪(git rm --cached) | logs/tsla_paper.pid、reports/tsla/loop.pid、reports/tsla/loop_output.log(1.9MB) | 运行时垃圾,违反卫生纪律;.gitignore 已补 `*.pid`/`logs/` |
| .gitignore 补缺口 | `*.pid`、`logs/`、`.env.*`(保留 .env.example)、`storage_state*.json` | 执行层登录态防泄漏前置(盘点发现裸 storage_state.json 可绕过原有 ignore) |
| 身份更新 | README 重写、pyproject 改名 quant-stock、.env.example 标 legacy 段 + PAPER_ONLY、CHANGELOG 加时代分隔 | fork 身份落地 |
| 依赖前置 | +yfinance、+exchange-calendars(C1 即用);ccxt/python-okx 标注 legacy 留至 C2 | 现在删 crypto 依赖会断 src/exchange 与其 13 个测试 |
| legacy banner | PROJECT_SPEC.md、STRATEGY_GUIDE.md、docs/CURRENT_STATE.md(QuantLab v1.1 快照,roster 引用故留原位) | CLAUDE.md 明示保留的文档,加横幅防误读 |
| frozen-dependency banner | src/strategies/minute_swing.py(注释,零逻辑改动) | 它是 minswing_v3_final 与 fast_exit 的信号内核,de-facto frozen,此前无任何标注 |
| 计划修正 | MIGRATION_PLAN:data_quality 从 🟢 改为"C1 须加交易日历感知"(24/7 假设会卡死美股 quality gate) | 盘点实证(checks.py missing_bars/latest_bar_delay) |

## 二、src/ 分流(本次未动,按 checkpoint 执行)

| 模块 | 判定 | 动作 | 关键依赖/风险 |
|---|---|---|---|
| `exchange/`(okx/ccxt/whale/news_sentiment) | crypto | **C2 退役**(整包归档+同 commit 处理 tests/exchange 13 个测试) | 被 v2.5A paper 链 + 20 脚本 + 3 测试文件引用,现在动必断 |
| `exchange/rate_limiter.py` | mixed | C2 时决定提取复用或随包归档 | 机制通用,默认 config 指 okx.yaml |
| `ingestion/base.py` | 通用 | **keep**,C2 yfinance ingestor 直接继承 | — |
| `ingestion/ohlcv.py + funding.py` | crypto | C2 换 yfinance+prices.db;funding 无美股对应物→归档 | — |
| `ingestion/universe.py` | crypto | **C1 替换**为美股 UniverseUpdater 时归档 | 近零依赖 |
| `factors/`(base/registry/technical) | 通用 | **keep** | — |
| `factors/derivatives.py` | crypto | C2 归档 + 同 commit 修 tests/factors/test_technical.py 的 import(注册副作用) | 非零依赖,单独动会断测试 |
| `backtest/`(engine/metrics/position_sizing/reports) | 通用 | **keep**;C3 调用侧传股票 costs/freq(注意 vectorbt 年化口径:24/7 vs 6.5h×252) | frozen 基线复现全靠它 |
| `backtest/costs.py` | crypto 常量 | **C3 新增 US_STOCK_* 预设;OKX_* 常量 frozen 不删不改值** | gates.py 八个 gate 硬编码 OKX_SPOT,是 frozen 验证输入 |
| `backtest/slippage.py` | mixed | keep;estimate_funding_cost 留作 crypto-only 死代码 | — |
| `backtest/standardized_output.py` | mixed | C2:-USDT 剥离改通用 ticker 规整,旧默认值保留兼容 | run_id 命名与 ledger 历史耦合 |
| `backtest/paper_session.py` | mixed | C5-C7 决定复用或新建 | 表 schema 与 research.duckdb 历史耦合 |
| `validation/`(gates/runner) | mixed | **C2(C4 前)**:costs/init_cash/freq 提为参数,默认值原样保留 | 硬编码是 MinSwing v3 准入的 frozen 输入 |
| `data_quality/` | mixed | **C1**:missing_bars/latest_bar_delay 加交易日历模式(新参数,默认行为不变) | 不加则美股周末停盘触发 critical fail |
| `storage/` | 通用 | **keep**;write_funding/funding 视图 C2 后为无害死代码 | 全仓依赖最广,绝不移动 |
| `research/db.py` | 通用 | **keep**(canonical DB 入口,红线 1) | — |
| `replay/` | crypto | **frozen 证据链,原地只读**(v2.3R/v2.4 exit-mode 决策复现基础);股票版 replay 另建 | 移动即破坏 tests/replay + 历史复现 |
| `risk/` | 通用代码 | keep;**C3/C4 新建 config/risk/us_stock_paper.yml,不改 small_account.yml**(288-bar 冷却隐含 24/7,直接套用=静默错参) | frozen 输入 |
| `analysis/` | mixed | C2:PRESET_EVENTS 换美股事件目录(财报/FOMC/CPI),框架保留 | 低优先级 |
| `news/` | 美股原生 | **keep**(TSLA Google News RSS+情绪词典);多 ticker 泛化按需 | 仓内少数已"股票原生"模块 |
| `notify/` | 通用 | keep;'Coin:' 文案执行层接入时参数化 | — |

## 三、策略库与 registry

- **frozen 生产候选(原位一字节不动)**:`minswing_v3_final.py`、`minute_swing_dual.py`、`extreme_reversal.py`、`short/short_session_filter.py`、`short/short_trend_follow.py`、`short/short_swing_trail.py`、`combo/fast_exit.py`、`base.py`;外加 de-facto frozen 的 `minute_swing.py`(已补 banner)。美股适配一律**派生新文件**。
- **short/ 与 combo/ 的 archive 状态文件**(short_rsi_overbought/short_swing/short_vol_atr/fund_mode/long_short_auto):**本次未移动**——`short_swing_trail.py` 在 CURRENT_STATE(生产候选 Top3)与 registry(archive)状态矛盾,待裁决;为保持移动规则简单,这两目录整体原位,C3 重新分流。
- `us_stock/tsla_news_event.py`:名为美股实为 OKX TSLA-USDT-SWAP 永续实验;保留作 us_stock 赛道种子,C1 接真实美股数据后修 import 重验,旧结论标 legacy。
- `registry/strategies.yml`:单一事实源,既有条目冻结;本次仅同步 4 处路径字符串(long/meta 新位置),状态/参数零改动;美股策略按同 schema 追加。

## 四、scripts/ 现役(16 个)

| 类别 | 脚本 | 动作 |
|---|---|---|
| 通用即用 | init_research_db、create_experiment、run_data_quality | keep,C1 即用 |
| 验证管线(方法论资产无关) | out_of_sample_test、walk_forward、monte_carlo、validate_strategy、run_backtest、event_backtest、build_data_manifest | C2 换数据喂入,C4 接门禁;**严禁改 gate 逻辑**。注意:out_of_sample_test/walk_forward/event_backtest/run_backtest 的扁平策略 import 自 fork 前(`3500d92`)已断,**当前不可直接运行**,C2 修复 |
| 数据采集 | bootstrap_data(C2 改写)、verify_okx(C2 替换并同步改 Makefile verify 目标) | C2 |
| v2.5A paper 链 | paper_runner、top50_paper_monitor(+对应 2 个测试) | decide-later:v2.5A/v2.6 promotion 收口确认后与测试成对归档 |
| 待评估 | tsla_factor_iterate | Strat 在 C3 评估是否移植因子迭代框架 |
| 运维 | restart_team.sh | keep |

## 五、配置与数据面

- **frozen 不动**:config/strategies/ 四个 yml、config/replay/v2_3_exit_mode_replay.yml、config/paper_observation/v2_3.yml、config/universe/(okx_top50、paper_candidate_pools——v2.5A 观察基线输入)、config/risk/small_account.yml、experiments/(rejected/ 永久保留作纪律证据)、reports/(frozen 决策证据链)。
- **C1 替换**:config/universe.yaml 原地改美股(零代码依赖,git 历史即存档);美股池建议同目录新文件 `config/universe/us_universe_YYYYMMDD.yml`。
- **C2 处理**:config/okx.yaml(随 src/exchange 退役)、config/settings.py 的 okx_* 字段(被 14 处引用,C2 前删会连锁断);股票侧字段可随时追加。
- **密钥红线现状(盘点实证)**:git 跟踪文件中无任何 duckdb/sqlite/parquet/env/cookie/auth;.gitignore 实测覆盖 data/、*.duckdb、*.sqlite、.env、.auth/、*cookies*.json、firstrade_state*.json、playwright/.auth/,本次再补 *.pid/logs//.env.*/storage_state*.json。

## 六、dashboard(10 页)

- **原样 keep(6)**:4_回测查看、6_研究总览、7_策略门禁、8_实验台账、9_数据质量、10_Paper验证(纯读 research.duckdb,资产无关)。
- **C2 换源(3)**:1_市场总览、3_因子表现(换源+文案);5_策略回测(C2 换数据/成本 + C3 接股票策略,期间标"加密遗留仅供查看";**绝不借改页面碰 frozen 策略**)。
- **已停用(1)**:2_资金费监控 → dashboard/legacy_pages/。
- app.py 文案改 quant-stock:C2 随换源一并(Dash 负责)。

## 七、遗留的待裁决项(decide-later 台账)

| # | 事项 | 谁/何时 |
|---|---|---|
| 1 | short_swing_trail.py 状态矛盾(CURRENT_STATE 生产候选 vs registry archive) | Lead+Audit,C3 前 |
| 2 | v2.5A paper 链(paper_runner/top50_monitor+测试)何时退役 | Lead,v2.6 promotion 收口后 |
| 3 | docs/V2_1、V2_3R、V2_5 三份报告是否移 docs/legacy/(须同步改 team/handoffs/valid.md,三份一致处理) | Lead+Valid,不急 |
| 4 | rate_limiter 提取复用 or 随 exchange 归档 | Data,C2 |
| 5 | paper_session 框架复用 or 新建 | Exec,C5-C7 |
| 6 | 4h 赛道(aggressive_momentum/ichimoku,OOS ROBUST)在美股是否立项 | Strat,C3 |
| 7 | README.md(src/strategies/ 内)提到 meta/dynamic_selector 为 proven 与 registry 矛盾 | Audit 核查 |
