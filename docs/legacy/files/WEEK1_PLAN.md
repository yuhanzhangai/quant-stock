# Week 1 推进计划 - 每天给 Claude Code 的指令

> 每天的指令独立可用,你可以直接复制给 Claude Code。
> 节奏建议:每天 2–3 小时实际投入,跑完当天任务再休息。

---

## Day 0(开工前):你自己要做的准备

不要让 Claude Code 替你做这些,先自己搞定:

1. **海外 VPS 或 VPN**:确认能稳定访问 `www.okx.com`
2. **OKX 账户**:KYC 完成,登录账户
3. **API Key**:
   - OKX → API → 创建 V5 API
   - **只勾 Read 权限**
   - 绑定 IP 白名单(VPS 的固定 IP,或本机出口 IP)
   - 设置 Passphrase(找密码管理器存好)
   - 保存 API Key、Secret、Passphrase 三件套
4. **本地环境**:
   - 安装 Python 3.11+
   - 安装 `uv`(`curl -LsSf https://astral.sh/uv/install.sh | sh`)
   - 安装 git
   - 准备一个空目录作为项目根目录
5. **把这两份文档放到项目根目录**:
   - `PROJECT_SPEC.md`
   - `CLAUDE.md`

---

## Day 1:项目骨架 + 网络验证

### 启动指令(复制给 Claude Code)

```
项目根目录已经放好 PROJECT_SPEC.md 和 CLAUDE.md。

请按以下流程开始:

1. 先完整读一遍这两份文档,用 5 句话以内告诉我:
   - 你对项目的理解
   - 你看到的潜在风险或不明确的地方
   - 第一天你打算完成什么

2. 等我确认后,开始今天的任务:
   - 用 uv 初始化 Python 3.11+ 项目(pyproject.toml)
   - 创建 PROJECT_SPEC 中定义的目录结构(空目录用 .gitkeep)
   - 创建 .env.example,包含:
     OKX_API_KEY、OKX_API_SECRET、OKX_PASSPHRASE、
     OKX_USE_SIMULATED(默认 0)、TELEGRAM_BOT_TOKEN(可选)、
     TELEGRAM_CHAT_ID(可选)、LOG_LEVEL(默认 INFO)
   - 创建 .gitignore(包含 data/, .env, *.duckdb, __pycache__, .pytest_cache 等)
   - 创建 README.md(项目简介 + 5 分钟启动指南占位)
   - 创建 Makefile,封装常用命令:install、verify、test、lint、format、dashboard

3. 安装基础依赖到 pyproject.toml:
   - 运行时:ccxt, python-okx, aiohttp, aiolimiter, tenacity, polars, pandas, 
     duckdb, pyarrow, pydantic-settings, loguru, pyyaml, python-dotenv
   - 开发:pytest, pytest-asyncio, ruff, pyright

4. 创建 scripts/verify_okx.py:
   - 调用 GET /api/v5/public/time 验证网络连通
   - 如果配了 API Key,再调用一个需要鉴权的接口验证(如 /api/v5/account/balance,只读)
   - 打印连通性、延迟、API Key 状态
   - 用 loguru 输出

5. 跑通 `make verify`,把输出贴给我

6. git init + 第一次 commit: "chore: initial project scaffold"

完成后告诉我:
- 已经搭好的文件列表
- make verify 的输出
- 任何遇到的问题
```

### 你要做的验证
- [ ] 目录结构对照 PROJECT_SPEC.md 没遗漏
- [ ] `.env.example` 字段完整
- [ ] `make verify` 能跑通,网络通、API Key 有效
- [ ] git log 有一条 commit

---

## Day 2:OKX 客户端封装

### 启动指令

```
Day 1 已完成,今天做 OKX 客户端封装。

任务:

1. 实现 src/exchange/rate_limiter.py:
   - 基于 aiolimiter 的限流器
   - 支持按接口名独立限流(配置驱动)
   - 默认配置写在 config/okx.yaml

2. 实现 src/exchange/ccxt_client.py:
   - 异步 CCXT OKX 客户端封装
   - 集成限流器和 tenacity 重试(最多 5 次,指数退避)
   - 统一日志(URL、参数、状态码、耗时)
   - 提供方法:fetch_tickers, fetch_ohlcv, fetch_markets
   - OHLCV 支持分页拉取大范围历史数据

3. 实现 src/exchange/okx_client.py:
   - 用 python-okx 封装资金费、持仓量等 OKX 特有接口
   - 同样集成限流和重试
   - 提供方法:fetch_funding_rate_history, fetch_open_interest

4. 配置文件 config/okx.yaml:
   - 各接口的限流配置
   - 默认请求超时
   - 是否使用模拟盘

5. 测试 tests/exchange/:
   - 用 pytest-asyncio
   - mock 真实的 OKX 响应(从真实 API 拉一次响应保存到 tests/fixtures/)
   - 测试覆盖:正常返回、限流退避、重试、错误处理

6. 写一个 scripts/test_client.py 演示:
   - 拉 BTC-USDT 最近 100 根 1h K 线
   - 拉 BTC-USDT-SWAP 最近资金费率
   - 打印结果

7. git commit: "feat(exchange): implement OKX clients with rate limiting"

完成后:
- 跑通 scripts/test_client.py,贴输出
- 跑通 pytest,贴测试通过截图(或文本)
- 总结今天的设计决策
```

### 你要做的验证
- [ ] `pytest` 全绿
- [ ] `python scripts/test_client.py` 能拉到真实数据
- [ ] 看一眼日志,确认限流、重试机制有触发记录
- [ ] code review 关键文件

---

## Day 3:数据采集 + 存储层

### 启动指令

```
Day 2 完成,今天做数据采集和存储。

任务:

1. 实现 src/storage/parquet_writer.py:
   - 写 Parquet,按 symbol/timeframe/year 分区
   - 支持增量追加 + 去重(按时间戳)
   - 用 polars 写,pyarrow 引擎

2. 实现 src/storage/duckdb_client.py:
   - DuckDB 客户端封装
   - 提供 execute、query_df、create_view 方法
   - 启动时自动创建必要的视图(让 DuckDB 直接 SELECT Parquet 文件)

3. 实现 src/storage/state_tracker.py:
   - SQLite 持久化采集状态
   - 表结构:ingestion_state(source, symbol, timeframe, last_timestamp, updated_at)
   - 提供 get_last_timestamp、update_last_timestamp 方法

4. 实现 src/ingestion/base.py:
   - IngestorBase 抽象基类
   - 定义接口:fetch、save、run(增量更新)
   - 包含通用日志、错误处理

5. 实现 src/ingestion/ohlcv.py:
   - OHLCVIngestor,继承 IngestorBase
   - 支持指定 symbol、timeframe、start、end
   - 增量更新:从 state_tracker 读 last_timestamp,只拉新数据
   - 落盘到 Parquet(按分区)
   - 更新 state_tracker

6. 实现 src/ingestion/universe.py:
   - 每天更新 OKX 现货 USDT 交易对 Top 100(按 24h 成交额)
   - 结果写到 SQLite 的 universe 表

7. 实现 scripts/bootstrap_data.py:
   - 一次性回填 BTC-USDT、ETH-USDT 的 1h、4h、1d K线,过去 2 年
   - 打印进度条(rich 库)

8. 测试:
   - tests/storage/、tests/ingestion/
   - 测试增量更新、断点续传、数据完整性

9. git commit: "feat(ingestion): implement OHLCV ingestion with incremental update"

完成后:
- 跑 bootstrap_data.py,实际拉 2 年数据(估计 5-10 分钟)
- 用 DuckDB 查一下数据条数、时间范围,贴结果
- 检查 data/parquet/ 目录结构是否合理
```

### 你要做的验证
- [ ] `data/parquet/ohlcv/spot/BTC-USDT/1h/` 下有 Parquet 文件
- [ ] 用 DuckDB 查 `SELECT COUNT(*), MIN(ts), MAX(ts) FROM ohlcv WHERE symbol='BTC-USDT'`,数字合理
- [ ] 再跑一次 bootstrap,确认是增量更新而不是重新拉
- [ ] 测试通过

---

## Day 4:因子库

### 启动指令

```
Day 3 数据已经入库,今天做因子库。

任务:

1. 实现 src/factors/base.py:
   - FactorBase 抽象基类
   - 接口:name, compute(df) -> Series, dependencies(需要哪些列)
   - 支持因子值缓存(写到 data/parquet/factors/)

2. 实现 src/factors/registry.py:
   - 因子注册表
   - 装饰器 @register_factor 自动注册
   - 提供 list_factors、get_factor、compute_all 方法

3. 实现 src/factors/technical.py(至少 5 个因子):
   - momentum_n(N 日收益率)
   - volatility_n(N 日年化波动率)
   - rsi_n(RSI)
   - volume_zscore_n(成交量 z-score)
   - atr_n(ATR)
   - 都用 polars / pandas-ta 实现

4. 实现 src/factors/derivatives.py(占位,先写 1 个):
   - funding_rate_ma(资金费率 N 日均值)
   - 数据从 funding 表读

5. 测试 tests/factors/:
   - 用 fixture 数据测因子计算正确性
   - 边界情况(数据不足、NaN 处理)

6. 实现 scripts/compute_factors.py:
   - 计算 BTC-USDT、ETH-USDT 的全部因子
   - 输出到 data/parquet/factors/

7. notebooks/examples/02_factor_research.ipynb:
   - 加载因子数据
   - 画一张因子值时序图
   - 计算因子之间的相关性矩阵

8. git commit: "feat(factors): implement basic factor library"

完成后:
- 列出所有已实现因子
- 贴 notebook 的因子相关性热图
```

### 你要做的验证
- [ ] 至少 5 个技术因子,每个都有测试
- [ ] notebook 能跑,图能画出来
- [ ] 因子值缓存生效(第二次计算很快)

---

## Day 5:回测引擎

### 启动指令

```
Day 4 因子库就绪,今天做回测引擎。

任务:

1. 实现 src/backtest/costs.py:
   - OKX 现货真实手续费:挂单 0.08%,吃单 0.10%
   - 永续费率:挂单 0.02%,吃单 0.05%
   - 滑点模型:可配置(默认市价单 5 bp)

2. 实现 src/backtest/engine.py:
   - 基于 vectorbt 封装
   - 接口:run(price_df, signal_df, init_cash, costs) -> Portfolio
   - 支持多标的同时回测
   - 支持参数搜索(grid search)

3. 实现 src/backtest/metrics.py:
   - 标准指标:累计收益、年化收益、夏普、索提诺、最大回撤、卡玛、胜率、盈亏比、换手率
   - 输出为 dict,方便落库

4. 实现 src/backtest/reports.py:
   - 生成回测报告(累计收益曲线、回撤曲线、月度收益热图、持仓分布)
   - 输出 HTML(用 plotly)
   - 报告存到 reports/ 目录

5. 实现 src/strategies/trend_ma.py:
   - 双均线策略(短均线上穿长均线开多,死叉平仓)
   - 参数:short_window、long_window、symbol、timeframe

6. 实现 scripts/run_backtest.py:
   - 跑 trend_ma 在 BTC-USDT 1h 上的回测
   - 参数搜索:short ∈ [5, 10, 20], long ∈ [50, 100, 200]
   - 输出报告

7. notebooks/examples/03_backtest_demo.ipynb:
   - 完整跑一遍 demo
   - 展示参数热力图

8. git commit: "feat(backtest): implement vectorbt engine with reports"

完成后:
- 跑 run_backtest.py,贴最优参数 + 关键指标
- 贴回测报告 HTML 截图(或描述)
```

### 你要做的验证
- [ ] 回测结果"看起来合理"(夏普别 > 5,最大回撤别 < 5%,小心 lookahead bias)
- [ ] 手续费有扣
- [ ] 报告能打开看

---

## Day 6:可视化面板

### 启动指令

```
Day 5 回测就绪,今天做 Streamlit 面板。

任务:

1. 实现 dashboard/app.py 主入口:
   - 多页面应用
   - 侧边栏:全局筛选(币种、时间范围)
   - 主页:项目简介

2. dashboard/pages/1_市场总览.py:
   - 当日 Top 涨跌(从 universe 表读)
   - 24h 成交额排行(柱状图)
   - 主流币 K 线缩略图(BTC、ETH、SOL)

3. dashboard/pages/2_资金费监控.py:
   - 永续合约资金费率排行表
   - 资金费率历史曲线(选币种)
   - 异常资金费高亮(>0.1% 或 <-0.1%)

4. dashboard/pages/3_因子表现.py:
   - 选因子、选币种
   - 画因子时序图 + 价格叠加
   - 显示因子统计(均值、标准差、当前 z-score)

5. dashboard/pages/4_回测查看.py:
   - 加载已有回测结果
   - 展示净值曲线、回撤、参数热力图

6. Makefile 加一条 `make dashboard`:streamlit run dashboard/app.py

7. README 更新"可视化面板"章节,加截图说明

8. git commit: "feat(dashboard): implement Streamlit pages for monitoring"

完成后:
- 跑 make dashboard
- 贴每个页面的截图(或描述效果)
```

### 你要做的验证
- [ ] 4 个页面都能打开,不报错
- [ ] 数据是实时从 DuckDB 读的(改了数据库立刻反映)
- [ ] 加载速度可接受(<3 秒)

---

## Day 7:CI + 文档 + 复盘

### 启动指令

```
Day 6 完成,今天收尾。

任务:

1. 创建 .github/workflows/ci.yaml:
   - Python 3.11
   - 步骤:checkout, setup uv, install, ruff check, ruff format --check, pytest
   - 触发:push、pull_request

2. 完善 README.md:
   - 项目介绍(从 PROJECT_SPEC 浓缩)
   - 5 分钟启动指南(step by step,任何人能跟着跑通)
   - 已实现功能列表
   - 截图或 GIF
   - 常见问题(中国网络、OKX API Key 申请要点)
   - License(MIT)

3. 检查 .env.example,确保所有需要的环境变量都列出来了

4. 跑一次完整端到端测试:
   - 删掉 data/ 目录
   - 走一遍:make verify → make install → 配置 .env → 
     scripts/bootstrap_data.py → scripts/compute_factors.py → 
     scripts/run_backtest.py → make dashboard
   - 把每一步的输出贴出来

5. 写 docs/RETROSPECTIVE.md:
   - 第一周做了什么
   - 哪些做得好
   - 哪些可以改进
   - 第二周建议(链上数据?更多因子?)

6. git commit: "docs: complete week 1 documentation and CI"
   git tag v0.1.0-mvp

完成后:
- CI 通过截图(或文本)
- 端到端测试输出
- 复盘文档
```

### 你要做的验证
- [ ] GitHub Actions 跑通
- [ ] README 完整,陌生人能跟着跑起来
- [ ] git tag 打上 v0.1.0-mvp

---

## 第一周完成后:你应该拥有什么

1. ✅ 一个可运行的本地研究系统
2. ✅ 2 年的 BTC/ETH 历史 K 线数据
3. ✅ 5+ 基础因子,可批量计算
4. ✅ vectorbt 回测引擎,能跑双均线策略并出报告
5. ✅ Streamlit 面板,4 个监控页面
6. ✅ 完整的测试 + CI
7. ✅ 详细的文档和复盘

**总投入**:7 天 × 2-3 小时/天 = 15-20 小时

**Claude Code 工作量**:大约 80-90% 的代码由它写,你做指挥、review、决策

---

## Week 2 预告(完成后再讨论)

- 接入更多币种(扩到 Top 50 永续)
- 资金费率长期归档
- 横截面动量策略
- 链上数据接入(Glassnode 免费层)
- 自动每日报告(发到 Telegram)
