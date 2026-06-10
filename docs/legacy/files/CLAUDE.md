# CLAUDE.md

> Claude Code 启动时自动读取。本文件是项目的"宪法",任何代码必须遵守。

## 项目背景

这是一个**研究型加密货币量化平台**,数据源是 **OKX**。
**只做研究和分析,绝不接交易执行**。
详细规划见 `PROJECT_SPEC.md`。

## 核心原则

1. **MVP 优先**:不要过度工程化。能用 50 行解决的不要写 500 行。
2. **小步推进**:一次只做一个模块,做完测试通过、commit、再下一个。
3. **不写就不要假装写了**:没实现的功能就明说,不要写 `# TODO` 占位然后假装完成。
4. **提问优于猜测**:需求不明确就问,不要瞎写。

## 技术栈(必须遵守)

- Python 3.11+
- 包管理:**uv**(不要用 pip / poetry)
- 配置:**pydantic-settings**(不要硬编码任何配置)
- 日志:**loguru**(不要用 print、不要用 logging 模块)
- 类型检查:**pyright**(所有公共函数必须有类型注解)
- 格式化:**ruff**(format + lint)
- 测试:**pytest + pytest-asyncio**
- 异步:**asyncio + aiohttp**
- 数据处理:**Polars 优先,Pandas 兼容**
- 存储:**DuckDB + Parquet**(不要默认用 PostgreSQL)

## 交易所对接

- 主源:**OKX V5 API**
- SDK 选择:
  - 常规 K 线、Ticker:**CCXT**(`ccxt.async_support`)
  - 资金费、持仓量、WebSocket:**python-okx**
- 永远只读,API Key 只勾 Read 权限
- 限流:每个接口独立限流器(`aiolimiter`)
- 失败重试:`tenacity`,指数退避,最多 5 次

## 文件 / 命名约定

- 模块文件:小写下划线 `funding_arb.py`
- 类:大驼峰 `FundingArbStrategy`
- 函数 / 变量:小写下划线 `fetch_ohlcv`
- 常量:大写下划线 `MAX_RETRIES`
- 私有:前缀下划线 `_internal_helper`
- 测试:`test_<module>.py`,放在 `tests/` 下

## 必须做的事

- [ ] 每个模块完成后跑 `pytest`,通过才 commit
- [ ] 每个公共函数有 docstring(Google 风格)
- [ ] 数据采集要有详细日志(URL、参数、状态码、耗时)
- [ ] 时间统一 UTC、毫秒时间戳
- [ ] 测试用 fixture 文件存真实 API 响应样本,不要写假数据
- [ ] 任何外部 API 调用必须 mock 后再测

## 严禁的事

- ❌ 把 API Key、Secret、Passphrase 写在代码里
- ❌ 写任何下单 / 提币 / 修改账户的代码
- ❌ 用 `print` 输出(用 `loguru`)
- ❌ 全局变量(用配置类或依赖注入)
- ❌ `bare except`(必须指定异常类型)
- ❌ 把 `data/`、`.env`、`*.duckdb` 提交到 git
- ❌ 一次写 5 个模块(每次只做一个)
- ❌ 跳过测试直接说"完成了"

## OKX 数据细节(避免踩坑)

### 命名
- 现货:`BTC-USDT`(连字符)
- 永续:`BTC-USDT-SWAP`
- 季度:`BTC-USDT-250926`(带到期日)
- CCXT 内部统一为 `BTC/USDT`,调原生 API 时要转换

### 时间
- 全部毫秒时间戳(13 位整数,UTC)
- ISO 时间戳格式:`2026-04-22T09:08:57.715Z`(签名要用)
- 服务器时间偏差 > 30 秒,签名失败

### K 线分页
- `history-candles` 一次最多 100 根
- 用 `before`(向旧)/`after`(向新)游标分页
- 拉 2 年 1h 数据 ≈ 17520 根 ≈ 176 次请求

### 资金费历史
- 官方接口最多返回 3 个月
- 想要长期数据必须自己每天采集存档

### 网络
- 中国大陆访问受限,用海外 VPS
- 启动前用 `GET /api/v5/public/time` 验证连通性

## 工作流程(每个模块都按这个走)

1. **设计**:先讲清楚要做什么、接口长什么样、有哪些边界情况
2. **等用户确认**(不要一上来就写代码)
3. **实现**:写代码 + docstring + 类型注解
4. **测试**:写 pytest 用例,跑通
5. **运行验证**:写一个示例脚本演示,实际跑一下
6. **文档**:在 README 对应章节加说明
7. **提交**:`git add` + `git commit`,信息用 conventional commits

## Conventional Commits 规范

- `feat:` 新功能
- `fix:` 修 bug
- `refactor:` 重构
- `test:` 加测试
- `docs:` 文档
- `chore:` 杂项(依赖、配置)

例:`feat(ingestion): implement OKX OHLCV ingestion with incremental update`

## 与用户协作

- 每个模块完成,用一段话总结:做了什么、怎么验证、下一步建议
- 有不确定的地方主动问,不要默认假设
- 如果发现规划文档有问题,提出来讨论,不要自作主张改架构
- 长任务定期同步进度,不要消失 20 分钟才出来

## 当前 MVP 范围

**第一周做这 8 件事**(详见 PROJECT_SPEC.md):
1. 环境就绪(uv, .env, verify_okx.py)
2. OKX 客户端封装
3. 数据采集(OHLCV + 资金费)
4. 存储层(DuckDB + Parquet)
5. 因子库(5 个基础因子)
6. 回测(vectorbt + 双均线 demo)
7. Streamlit 面板(3 个页面)
8. 测试 + CI

不在范围内的不要做:
- 链上数据
- 期权数据
- Prefect 调度
- 实时 WebSocket 推送
- 多策略组合优化
