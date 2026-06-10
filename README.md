# quant-stock — 美股量化研究 + Firstrade 全自动模拟盘

> 由 QuantLab(加密货币量化研究系统)fork 改造而来。资产改为美股,新增 Firstrade 模拟盘自动执行层。
> **研究 + 模拟盘验证工具,绝非投资建议。模拟盘无真金。**

## 两条腿

1. **研究脑**(复用 QuantLab 引擎):数据采集 → 因子 → 策略 → 回测(vectorbt)→ 验证管线 + 策略准入门禁 → experiment ledger
2. **执行手**(本项目新建):Firstrade 网页模拟盘浏览器自动化(Playwright 模拟真人,不走券商 API),策略信号 → 自动下单 → 成交回采对账。PAPER_ONLY 硬钉 + kill-switch。

## 快速入口

| 想了解 | 看哪里 |
|---|---|
| 项目宪法 / 红线 / 团队 | `CLAUDE.md` |
| crypto→stock 改造路线(checkpoint) | `docs/MIGRATION_PLAN.md` |
| 全仓资产分流清单(留/换/归档) | `docs/MIGRATION_MANIFEST.md` |
| 团队进度 | `team/PROGRESS_LOG.md` |
| 策略状态单一事实源 | `registry/strategies.yml` |
| 策略准入门禁政策 | `docs/STRATEGY_GATE_POLICY.md` |
| QuantLab 时代历史文档 | `docs/legacy/` |

## 5 分钟启动

前置:Python 3.11+ · [uv](https://docs.astral.sh/uv/)

```bash
make install     # uv sync --all-extras
make test        # pytest(基线:全部通过;replay 组缺本地数据时 skip 属正常)
make dashboard   # Streamlit 研究控制台
```

注:`make verify`(OKX 连通性)为 crypto 遗留,C2 数据层切换后将替换为美股数据源校验。

## 项目结构

```
├── config/            # 配置(universe / 策略 yml / risk;crypto 旧配置冻结存证)
├── src/
│   ├── ingestion/     # 数据采集(C2 切 yfinance + prices.db)
│   ├── exchange/      # [legacy] OKX 客户端,C2 退役
│   ├── storage/       # DuckDB + Parquet
│   ├── factors/       # 因子库(technical 通用;derivatives 为 crypto 遗留)
│   ├── strategies/    # 策略(root + short/ + combo/ 含 frozen 基线;归档见 archive/)
│   ├── backtest/      # 回测引擎(vectorbt)
│   ├── validation/    # 验证管线 + 准入门禁
│   ├── replay/        # [frozen 证据链] MinSwing v3 exit-mode replay,只读
│   └── research/      # experiment ledger(canonical DB 入口,只归 Lead)
├── dashboard/         # Streamlit 面板(legacy_pages/ 为停用页)
├── scripts/           # 现役脚本(crypto 运行时已移 archive/scripts/)
├── archive/           # 归档代码(只移动不修改,git 历史可溯源)
├── docs/              # 现行文档 + legacy/(QuantLab 时代)
├── registry/          # 策略注册表
├── experiments/       # 实验台账(rejected/ 永久保留作纪律证据)
├── team/              # 团队协作(roster / 进度 / 审计协议)
└── tests/             # 测试
```

## 技术栈

| 类别 | 工具 |
|------|------|
| 包管理 | uv |
| 数据存储 | DuckDB + Parquet |
| 数据处理 | Polars / Pandas |
| 回测 | vectorbt |
| 可视化 | Streamlit + Plotly |
| 数据源 | yfinance + stock-picker prices.db(C2 起;CCXT/python-okx 为 legacy,C2 移除) |
| 执行层 | Playwright(Firstrade 模拟盘,仅 paper) |

## 红线(详见 CLAUDE.md)

- 绝不写真实下单代码,执行层只对 Firstrade **模拟盘**,可一键停
- frozen 策略基线(MinSwing v3 等加密时代验证结果)只读存证,不改不复活
- 反过拟合纪律:可复现 + OOS + 成本/滑点/压力测试 + 门禁,不只看 Sharpe
- 密钥/登录态/数据库一律不入 git

## License

MIT
