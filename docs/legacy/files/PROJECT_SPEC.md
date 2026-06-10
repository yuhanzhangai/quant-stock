# 加密货币量化研究系统 - 项目规划 (OKX 版)

## 项目目标

搭建一个**研究和分析用**的加密货币量化平台,**不接交易执行**。
核心目标:
- 自动化采集和存储 OKX 历史/实时市场数据
- 提供因子计算、回测、策略分析能力
- 输出可视化报告和市场监控面板

## 边界

**做**:
- OKX 公开数据采集、存储、处理
- 因子和策略研究
- 回测和归因分析
- 可视化和监控

**不做**:
- 实盘下单
- 资金托管
- 高频交易基础设施
- API Key 任何写权限(只读)

## 交易所选择:OKX

### 为什么选 OKX
- 现货 + 合约 + 期权全品类覆盖,衍生品数据丰富
- API 文档完善,V5 接口稳定
- WebSocket 免费且性能好
- 资金费、持仓量等衍生品数据有公开接口

### 数据源
- **主源**:OKX V5 API(`https://www.okx.com/api/v5/`)
- **官方文档**:https://www.okx.com/docs-v5/zh/
- **辅助**:后期可加 Glassnode(链上)、CoinGlass(长期资金费)

## 技术栈

### 编程语言
- Python 3.11+

### 交易所 SDK
- **CCXT**(主力):统一接口,处理常规 K 线、Ticker
- **python-okx**(官方):资金费、持仓量、期权、WebSocket
- 在 `src/ingestion/base.py` 抽象,具体模块按需选择

### 数据采集辅助
- aiohttp(异步 HTTP)
- aiolimiter(限流器)
- tenacity(失败重试)

### 数据存储
- DuckDB(主数据库,做查询)
- Parquet 文件(原始数据存储,按 symbol/timeframe/year 分区)
- SQLite(回测结果、元数据、采集状态)

### 数据处理
- Polars(主要)
- Pandas(vectorbt 等需要)
- pyarrow(Parquet 读写)

### 回测和因子分析
- vectorbt(向量化回测,主力)
- Alphalens-reloaded(因子检验)
- TA-Lib / pandas-ta(技术指标)

### 可视化
- Streamlit(交互面板)
- Plotly(图表)
- mplfinance(K 线图)

### 任务调度
- 初期:cron + Python 脚本
- 后期:Prefect(可选)

### 通知
- python-telegram-bot

### 开发工具
- uv(包管理)
- JupyterLab(交互探索)
- pytest + pytest-asyncio
- ruff(格式化 + lint)
- pyright(类型检查)
- loguru(日志)
- pydantic-settings(配置)

## OKX API 关键约束

### 鉴权
- API Key 三件套:`OK-ACCESS-KEY` + `OK-ACCESS-SIGN` + `OK-ACCESS-PASSPHRASE`
- 签名:HMAC SHA256 + Base64
- 时间戳:UTC ISO 格式(如 `2026-04-22T09:08:57.715Z`)
- 时间偏差超过 30 秒请求失败,VPS 必须开 NTP

### 权限设置
- **只勾 Read**,绝不勾 Trade / Withdraw
- 绑定 IP 白名单(必须,否则 Key 14 天失效)
- Passphrase 自己保管,丢失只能重建

### 限流
| 接口 | 限频 |
|---|---|
| `market/candles` | 40 次 / 2 秒 |
| `market/history-candles` | 20 次 / 2 秒 |
| `market/tickers` | 20 次 / 2 秒 |
| `public/funding-rate-history` | 10 次 / 2 秒 |
| `public/open-interest` | 20 次 / 2 秒 |

每个接口独立限流器,429 自动指数退避。

### 数据特性
- 币对命名:`BTC-USDT`(现货)、`BTC-USDT-SWAP`(永续)、`BTC-USDT-250926`(交割)
- 时间戳:毫秒级 UTC(13 位整数)
- 历史 K 线分页:每次 100 根,用 `before`/`after` 游标
- 资金费率历史:**官方接口最多 3 个月**,长期数据要自建归档

### 网络
- 中国大陆需要海外 VPS(推荐 Vultr 东京、AWS 新加坡)
- 启动前用 `GET /api/v5/public/time` 验证连通

## 数据范围

### 第一阶段(MVP - 第一周)
- **品种**:OKX 现货 USDT 交易对 Top 100(按 24h 成交额)
- **K 线**:1h, 4h, 1d(三个周期)
- **历史长度**:过去 2 年
- **辅助**:BTC/ETH 永续 + 资金费率(仅最近 3 个月)

### 第二阶段(第二周)
- 完整持仓量历史
- 全市场资金费率(自建归档,每天写入)
- Top 30 永续合约的 K 线

### 第三阶段(后续)
- 期权数据(Deribit/OKX)
- 链上数据(Glassnode 免费层、Etherscan)
- L2 订单簿快照(可选,Tardis.dev 付费)

## 项目目录结构

```
crypto-research/
├── .env.example              # 环境变量模板(含 OKX_API_KEY 等)
├── .gitignore
├── README.md
├── CLAUDE.md                 # Claude Code 持久指令
├── pyproject.toml            # uv 配置
├── Makefile
│
├── config/
│   ├── settings.py           # pydantic-settings 全局配置
│   ├── okx.yaml              # OKX 接口、限流配置
│   └── universe.yaml         # 标的池定义
│
├── data/                     # gitignore
│   ├── raw/                  # 原始 JSON
│   ├── parquet/              # 处理后 Parquet(按 symbol/tf 分区)
│   │   ├── ohlcv/
│   │   │   ├── spot/
│   │   │   │   └── BTC-USDT/
│   │   │   │       ├── 1h/2024.parquet
│   │   │   │       └── 1h/2025.parquet
│   │   │   └── swap/
│   │   ├── funding/
│   │   └── open_interest/
│   ├── research.duckdb       # 主 DuckDB 库
│   └── meta.sqlite           # 采集状态、回测元数据
│
├── src/
│   ├── exchange/             # OKX 客户端封装
│   │   ├── okx_client.py     # python-okx 封装
│   │   ├── ccxt_client.py    # CCXT 封装
│   │   └── rate_limiter.py   # 限流器
│   │
│   ├── ingestion/            # 数据采集
│   │   ├── base.py           # 采集基类(IngestorBase)
│   │   ├── ohlcv.py          # K 线采集(用 CCXT)
│   │   ├── funding.py        # 资金费(用 python-okx)
│   │   ├── open_interest.py  # 持仓量
│   │   ├── universe.py       # 标的池更新
│   │   └── scheduler.py      # 调度入口
│   │
│   ├── storage/              # 存储抽象
│   │   ├── duckdb_client.py
│   │   ├── parquet_writer.py
│   │   ├── state_tracker.py  # 采集状态(已拉到哪个时间点)
│   │   └── schema.sql
│   │
│   ├── factors/              # 因子库
│   │   ├── base.py
│   │   ├── technical.py      # MA, RSI, ATR, Bollinger
│   │   ├── derivatives.py    # 资金费偏度、基差
│   │   ├── microstructure.py # 量价关系
│   │   └── registry.py
│   │
│   ├── strategies/           # 策略
│   │   ├── base.py
│   │   ├── momentum_xs.py    # 横截面动量
│   │   ├── funding_arb.py    # 资金费套利(纸上模拟)
│   │   └── trend_ma.py       # 趋势均线
│   │
│   ├── backtest/
│   │   ├── engine.py         # vectorbt 封装
│   │   ├── metrics.py
│   │   ├── reports.py
│   │   └── costs.py          # OKX 真实手续费/滑点模型
│   │
│   ├── analysis/
│   │   ├── factor_test.py    # IC, IR, 分组测试
│   │   ├── correlation.py
│   │   └── market_regime.py  # 市场状态分类
│   │
│   └── notify/
│       └── telegram.py
│
├── dashboard/
│   ├── app.py
│   └── pages/
│       ├── 1_市场总览.py
│       ├── 2_资金费监控.py
│       ├── 3_因子表现.py
│       └── 4_回测查看.py
│
├── notebooks/
│   └── examples/
│       ├── 01_load_data.ipynb
│       ├── 02_factor_research.ipynb
│       └── 03_backtest_demo.ipynb
│
├── reports/                  # 输出报告(Quarto/HTML)
│
├── tests/
│   ├── conftest.py
│   ├── fixtures/             # 真实 API 响应样本
│   └── test_*.py
│
└── scripts/
    ├── verify_okx.py         # 网络/API 连通性测试
    ├── bootstrap_data.py     # 初始化数据
    └── backfill.py           # 历史回填
```

## MVP 验收标准

### 第一周

#### 1. 环境就绪
- [ ] `uv sync` 一键安装所有依赖
- [ ] `.env.example` 包含所有需要的环境变量
- [ ] `python scripts/verify_okx.py` 验证 OKX API 可达
- [ ] README 有"5 分钟启动"指南

#### 2. OKX 客户端
- [ ] CCXT 客户端封装(异步、限流、重试)
- [ ] python-okx 客户端封装(资金费、持仓量)
- [ ] 限流器实现(分接口独立限流)
- [ ] 单元测试 mock 真实响应

#### 3. 数据采集
- [ ] 标的池采集(每天更新 Top 100)
- [ ] OHLCV 采集:能从 OKX 拉 BTC-USDT/ETH-USDT 1h K 线 2 年
- [ ] 资金费采集:BTC/ETH 永续最近 3 个月
- [ ] 增量更新(基于状态追踪,不重复拉)
- [ ] 失败重试 + 限流退避
- [ ] 数据落 Parquet,DuckDB 能直接 SELECT

#### 4. 存储层
- [ ] Parquet 按 `symbol/timeframe/year` 分区
- [ ] DuckDB 视图直接读 Parquet
- [ ] 采集状态持久化到 SQLite
- [ ] 数据完整性校验(无重复、无缺失日期)

#### 5. 因子库(至少 5 个)
- [ ] 动量(N 日收益)
- [ ] 波动率(N 日年化波动率)
- [ ] RSI
- [ ] 量价关系(成交量 z-score)
- [ ] ATR
- [ ] 因子注册和批量计算
- [ ] 因子值缓存

#### 6. 回测
- [ ] vectorbt 跑通双均线策略
- [ ] 标准报告:累计收益、回撤、夏普、胜率、换手率
- [ ] OKX 真实手续费(挂单 0.08% / 吃单 0.10%)
- [ ] 滑点模型(可配置)

#### 7. 可视化
- [ ] Streamlit 主页:市场总览(Top 涨跌、成交额)
- [ ] 资金费监控页:Top 永续合约的资金费排行
- [ ] K 线 + 指标查看页

#### 8. 测试
- [ ] 数据采集核心模块单元测试
- [ ] 因子计算正确性测试
- [ ] CI(GitHub Actions):lint + test

## 编码规范

- 类型注解:全部公共函数加(pyright 检查)
- docstring:Google 风格
- 异步:I/O 密集用 `asyncio`,CPU 密集用同步
- 错误处理:数据采集失败必须详细日志(含 URL、参数、错误码)
- 配置:全部走 `pydantic-settings`,不硬编码
- 日志:`loguru`,按天滚动到 `logs/`
- 测试:核心模块覆盖率 > 70%,API 调用必须 mock
- 时间:统一用 UTC、毫秒时间戳,显示时再转本地

## 安全要求

- API Key 只读,绝不开 Trade/Withdraw
- API Key 必须从 `.env` 读取,绝不硬编码
- `.env`、`*.duckdb`、`data/` 加入 `.gitignore`
- IP 白名单绑定后再用 Key
- VPS 上 SSH 用 Key 认证,不要密码
- Passphrase 单独保管(密码管理器)

## 后续扩展(不在 MVP)

- 链上数据(Glassnode、Etherscan)
- 长期资金费历史归档(每天自动追加)
- 期权数据(Deribit + OKX)
- Prefect 任务调度
- 多策略组合优化
- 自动化研究报告(Quarto)
- 实时面板(WebSocket 推送)
