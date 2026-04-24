# Research Database Schema

> Database: `data/meta/research.duckdb`
> Schema version: 1.0.0
> Init script: `python scripts/init_research_db.py`

## Tables

### strategy_registry

记录每个策略版本的状态。

| Column | Type | Description |
|--------|------|-------------|
| strategy_name | TEXT PK | 策略名称 |
| strategy_version | TEXT PK | 版本号 |
| status | TEXT | production / candidate / research / archive |
| direction | TEXT | long / short |
| timeframe | TEXT | 5m / 4h / 1d |
| symbols | TEXT | 适用币种列表 (JSON) |
| config_path | TEXT | 配置文件路径 |
| code_path | TEXT | 代码文件路径 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |
| notes | TEXT | 备注 |

### experiment_runs

每次实验的登记表。先写假设，再跑实验。

| Column | Type | Description |
|--------|------|-------------|
| run_id | TEXT PK | 实验 ID (YYYYMMDD_strategy_purpose_seq) |
| experiment_name | TEXT | 实验名称 |
| strategy_name | TEXT | 策略名称 |
| strategy_version | TEXT | 策略版本 |
| hypothesis | TEXT | 假设（实验前写） |
| params_hash | TEXT | 参数哈希（可复现） |
| params_json | TEXT | 参数 JSON |
| config_path | TEXT | 配置文件路径 |
| code_commit | TEXT | Git commit hash |
| data_version | TEXT | 数据版本 (manifest_YYYYMMDD) |
| train_start | TIMESTAMP | 训练集开始 |
| train_end | TIMESTAMP | 训练集结束 |
| test_start | TIMESTAMP | 测试集开始 |
| test_end | TIMESTAMP | 测试集结束 |
| cost_model | TEXT | 成本模型名称 |
| slippage_model | TEXT | 滑点模型名称 |
| status | TEXT | created / running / completed / failed / rejected / accepted / inconclusive |
| conclusion | TEXT | 实验结论（实验后写） |
| created_at | TIMESTAMP | 创建时间 |
| notes | TEXT | 备注 |

### backtest_runs

每次回测的标准化结果。

| Column | Type | Description |
|--------|------|-------------|
| backtest_id | TEXT PK | 回测 ID |
| run_id | TEXT FK | 关联的实验 ID |
| strategy_name | TEXT | 策略名称 |
| symbol | TEXT | 币种 |
| timeframe | TEXT | 时间框架 |
| start_ts | TIMESTAMP | 回测开始时间 |
| end_ts | TIMESTAMP | 回测结束时间 |
| initial_cash | DOUBLE | 初始资金 (default 50) |
| fee_model | TEXT | 费用模型 |
| slippage_model | TEXT | 滑点模型 |
| net_return | DOUBLE | 净收益率 |
| sharpe | DOUBLE | Sharpe ratio |
| sortino | DOUBLE | Sortino ratio |
| calmar | DOUBLE | Calmar ratio |
| max_drawdown | DOUBLE | 最大回撤 |
| profit_factor | DOUBLE | 盈亏比 |
| win_rate | DOUBLE | 胜率 |
| expectancy | DOUBLE | 期望值 |
| trade_count | INTEGER | 交易次数 |
| avg_trade_return | DOUBLE | 平均交易收益 |
| median_trade_return | DOUBLE | 中位交易收益 |
| max_consecutive_losses | INTEGER | 最大连亏次数 |
| created_at | TIMESTAMP | 创建时间 |

### validation_results

每个 gate 的验证结果。

| Column | Type | Description |
|--------|------|-------------|
| validation_id | TEXT PK | 验证 ID |
| run_id | TEXT FK | 关联的实验 ID |
| gate_name | TEXT | Gate 名称 (data_quality / cost_stress / oos / walk_forward / random_baseline / event_backtest / monte_carlo / parameter_stability) |
| status | TEXT | pass / fail / warning / skipped / error |
| score | DOUBLE | 得分 |
| threshold | DOUBLE | 阈值 |
| details_json | TEXT | 详细结果 JSON |
| created_at | TIMESTAMP | 创建时间 |

### data_manifest

数据文件追踪表。

| Column | Type | Description |
|--------|------|-------------|
| file_path | TEXT PK | 文件路径 |
| dataset | TEXT | 数据集 (ohlcv / funding) |
| venue | TEXT | 交易所 |
| inst_type | TEXT | 合约类型 (spot / swap) |
| symbol | TEXT | 币种 |
| timeframe | TEXT | 时间框架 |
| start_ts | TIMESTAMP | 数据起始时间 |
| end_ts | TIMESTAMP | 数据结束时间 |
| row_count | INTEGER | 行数 |
| checksum | TEXT | 文件校验和 |
| schema_version | TEXT | Schema 版本 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |
| ingest_run_id | TEXT | 采集批次 ID |

### data_quality_checks

数据质量检查结果。

| Column | Type | Description |
|--------|------|-------------|
| check_id | TEXT PK | 检查 ID |
| data_version | TEXT | 数据版本 |
| dataset | TEXT | 数据集 |
| symbol | TEXT | 币种 |
| timeframe | TEXT | 时间框架 |
| start_ts | TIMESTAMP | 数据起始时间 |
| end_ts | TIMESTAMP | 数据结束时间 |
| check_name | TEXT | 检查名称 (duplicate_ts / missing_bars / ohlc_validity / volume_validity / price_jump / latest_delay) |
| status | TEXT | pass / fail / warning |
| severity | TEXT | critical / warning |
| issue_count | INTEGER | 问题数量 |
| details_json | TEXT | 详细结果 JSON |
| created_at | TIMESTAMP | 创建时间 |

### paper_sessions

模拟交易会话追踪。

| Column | Type | Description |
|--------|------|-------------|
| session_id | TEXT PK | 会话 ID |
| strategy_name | TEXT | 策略名称 |
| strategy_version | TEXT | 策略版本 |
| config_path | TEXT | 配置文件路径 |
| data_version | TEXT | 数据版本 |
| start_ts | TIMESTAMP | 开始时间 |
| end_ts | TIMESTAMP | 结束时间 |
| initial_equity | DOUBLE | 初始权益 (default 50) |
| final_equity | DOUBLE | 最终权益 |
| total_signals | INTEGER | 总信号数 |
| accepted_trades | INTEGER | 接受交易数 |
| rejected_signals | INTEGER | 拒绝信号数 |
| net_pnl | DOUBLE | 净盈亏 |
| sharpe | DOUBLE | Sharpe ratio |
| max_drawdown | DOUBLE | 最大回撤 |
| status | TEXT | active / completed / stopped |
| created_at | TIMESTAMP | 创建时间 |
| notes | TEXT | 备注 |

## Usage

```bash
# Initialize (first time)
python scripts/init_research_db.py

# Initialize + seed strategies from registry
python scripts/init_research_db.py --seed

# Reset and recreate (caution: drops all data)
python scripts/init_research_db.py --reset
```

## Query Examples

```sql
-- Current production strategies
SELECT * FROM strategy_registry WHERE status = 'production';

-- All experiments for a strategy
SELECT * FROM experiment_runs WHERE strategy_name = 'minswing_v3' ORDER BY created_at;

-- Validation gate results for a run
SELECT gate_name, status, score, threshold FROM validation_results WHERE run_id = 'xxx';

-- Data coverage by symbol
SELECT symbol, timeframe, MIN(start_ts), MAX(end_ts), SUM(row_count) FROM data_manifest GROUP BY symbol, timeframe;
```
