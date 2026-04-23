# Crypto Research - 加密货币量化研究系统

基于 OKX 数据的研究型加密货币量化平台。**只做研究和分析，不接交易执行。**

## 功能

- OKX 历史/实时市场数据自动采集（K线、资金费率、持仓量）
- 因子计算与分析（动量、波动率、RSI、ATR 等）
- 向量化回测引擎（基于 vectorbt）
- Streamlit 可视化监控面板

## 5 分钟启动

### 前置条件

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器
- OKX API Key（只读权限）

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd crypto-research

# 安装依赖
make install

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 OKX API Key 三件套

# 验证 OKX 连通性
make verify

# 运行测试
make test

# 启动面板
make dashboard
```

## 项目结构

```
├── config/          # 配置文件
├── src/
│   ├── exchange/    # OKX 客户端封装
│   ├── ingestion/   # 数据采集
│   ├── storage/     # 存储层（DuckDB + Parquet）
│   ├── factors/     # 因子库
│   ├── strategies/  # 策略
│   ├── backtest/    # 回测引擎
│   └── analysis/    # 分析工具
├── dashboard/       # Streamlit 面板
├── scripts/         # 工具脚本
├── tests/           # 测试
└── notebooks/       # Jupyter 笔记本
```

## 技术栈

| 类别 | 工具 |
|------|------|
| 包管理 | uv |
| 数据存储 | DuckDB + Parquet |
| 数据处理 | Polars / Pandas |
| 回测 | vectorbt |
| 可视化 | Streamlit + Plotly |
| 交易所 SDK | CCXT + python-okx |

## License

MIT
