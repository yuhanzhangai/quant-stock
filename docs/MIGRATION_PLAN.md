# MIGRATION_PLAN —【已废止存档】QuantLab(crypto)→ quant-stock 研究路线

> ⚠️ **本路线已于 2026-06-10 随项目转向废止**(operator 拍板:转向博主跟单+下单留档,研究/回测层归档 `archive/`)。现行路线见 `docs/ROADMAP.md`。本文件仅作历史存证。

(以下为废止前原文)

> 沿用 QuantLab 的 checkpoint 纪律:**一次只做一个 · 必须有产出物 · 必须能复现 · 没过标准不进下一步 · 不只看 Sharpe。**
> Lead 驱动并把关(强制审核制度已由 operator 于 2026-06-10 废止;commit 前自检 + Lead 合并把关)。

## 判断:改造,不重做(operator 已拍板 2026-06-10)
QuantLab 是高质量项目(2.5万行/49测试过/真生产策略 MinSwing v3 Sharpe+2.13/自检出3个过拟合并归档/完整验证管线+门禁)。**核心引擎资产无关**,加密耦合只在少数文件。改造省 ~80% 工作量。

## 改造分层(谁要改、谁原样留)

### 🔴 要换(资产相关)
- `src/exchange/` OKX/CCXT 客户端、`funding.py`、`derivatives.py` 因子、`whale_detector` → **删/停**,换美股数据源。
- `src/ingestion/` OHLCV 采集 → 改 **yfinance + stock-picker `~/.stock-picker-mcp/prices.db`** 复用(无需券商 API)。
- `config/universe.yaml` 24 币种 → **美股 universe**(可用 stock-picker 诚实榜 PROVEN 喊单 + screener 选股池)。
- 时间/命名:UTC 毫秒 → 美股交易日/时区(美东)、ticker 命名(`NVDA` 非 `BTC-USDT`)、考虑停牌/拆股/分红。

### 🟢 原样留(资产无关,直接复用)
- `src/backtest/` 引擎/成本/标准化输出/position_sizing/metrics
- `src/validation/` gates、`src/factors/technical.py`(动量/RSI/ATR/MACD 全通用)
- `src/strategies/base.py` + MinSwing 框架(逻辑通用,只换数据喂入)
- `src/research/db.py` experiment ledger、`src/storage/`(DuckDB+Parquet)
- ~~`src/data_quality/`~~ **修正(2026-06-10 盘点发现)**:checks.py 的 missing_bars/latest_bar_delay 内置 24/7 连续交易假设,美股隔夜/周末停盘会被判大缺口直接 critical fail → **C1 须加交易日历感知模式(新参数,默认行为不变保历史复现)**,否则 C1 的 quality gate 必卡死。框架其余部分照常复用。
- `dashboard/` Streamlit(改数据源即可)

### 🆕 新建(执行层,本项目核心增量)
- `src/execution/firstrade_agent/` —— **Firstrade 模拟盘浏览器自动化 agent**(Playwright,模拟真人:随机人类节奏、登录态复用、单账号、可一键停)。
- `src/execution/order_router.py` —— 策略信号 → 模拟盘下单意图 → agent 执行 → 成交回采对账。
- 安全闸:`PAPER_ONLY=1` 硬钉(防误触真金)、kill-switch、每单 operator 可审计日志。

## Checkpoints(按序,一次一个)
- **C0 冻结基线** ✅(fork 自 QuantLab b85e77e,全历史保留)
- **C1 标的池转股票**:universe.yaml 换美股(种子=诚实榜 PROVEN + 流动性过滤);data quality gate 跑通(含给 data_quality checks 加 NYSE/Nasdaq 交易日历感知,见上方修正)。
- **C2 数据层转股票**:yfinance/prices.db 喂 ingestion;technical 因子在股票日线/分钟线上跑通;OOS 数据切分。
- **C3 策略在股票上复算**:MinSwing 框架喂股票数据,回测+成本+滑点;**看是否仍有 edge**(很可能要重调,股票≠加密的微观结构)。诚实:没 edge 就明说,不放水。
- **C4 验证管线+门禁**:OOS/Monte Carlo/压力测试在股票上跑;策略准入门禁。
- **C5 Firstrade agent(读)**:agent 能登录 Firstrade 模拟盘、读持仓/行情/账户,稳定不被检测。
- **C6 Firstrade agent(写)**:agent 能在模拟盘自动下单+成交回采对账;PAPER_ONLY 硬钉+kill-switch+人类节奏。
- **C7 信号→执行闭环**:策略出信号 → 自动模拟盘下单 → 记录 → 与回测对账(实盘滑点 vs 回测假设)。
- **C8 全自动循环 + 监控**:定时跑、Dashboard 监控模拟盘盈亏/成交/agent 健康;医生守 pane。

## 关键风险/诚实点
- **加密策略未必迁得到股票**:Hurst、时段效应、波动结构都不同;C3 大概率要重新研究 edge,**别假设 MinSwing 在股票上还 +2.13**。
- **Firstrade 浏览器自动化**:模拟盘无真金故风险低,但 UI 变更/检测/卡死是工程风险;出错先停。
- **不走 API 的代价**:慢、脆、要维护选择器;但符合 operator"模拟真人不走 API"的要求,且模拟盘没有 API 滥用封号的真金后果。
