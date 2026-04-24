# Daily Workflow

> 单人长期维护纪律系统。

## 每日检查清单（5分钟）

不做深度研究，只检查系统状态：

```bash
python scripts/run_data_quality.py --all         # 数据是否有问题
python scripts/market_health.py                    # 市场状态
python scripts/strategy_monitor.py                 # 策略健康度
```

- [ ] 数据是否更新（latest_bar_delay）
- [ ] OKX API 是否正常
- [ ] data_quality 是否有 critical
- [ ] production 策略是否有异常信号
- [ ] paper trader 是否正常记录
- [ ] Telegram 通知是否正常

## 每轮研究清单

每次只允许一个主实验。

### 开始前

```bash
python scripts/create_experiment.py --name <name> --strategy <strategy>
```

- [ ] 写 experiment.yml 中的 hypothesis
- [ ] 固定数据范围
- [ ] 固定参数搜索范围
- [ ] 固定 success criteria
- [ ] 确认没有使用测试集调参

### 运行中

```bash
python scripts/validate_strategy.py --strategy <name> --symbol <sym>
```

- [ ] 记录 run_id
- [ ] 记录 data_version
- [ ] 记录 code_commit
- [ ] 保存 config、metrics、trades、equity

### 结束后

```bash
python scripts/create_experiment.py --conclude <name> --status <status> --reason "<reason>"
```

- [ ] accepted / rejected / inconclusive 三选一
- [ ] 更新 strategy card（如果 accepted）
- [ ] 更新 registry/strategies.yml
- [ ] 如果 rejected，记录原因

## 每次策略修改前检查

- [ ] 这是 bug fix 还是 research change？
- [ ] 是否需要新 strategy_version？
- [ ] 是否需要新 experiment？
- [ ] 是否会影响历史结果复现？
- [ ] 是否会改变 production 参数？

如果改变 production 参数：
1. strategy_version +1
2. 新建 experiment
3. 重新跑 validation pipeline

## 每次上线 paper trading 前检查

- [ ] strategy status = candidate
- [ ] validation_report 存在
- [ ] risk config 存在
- [ ] paper config 存在
- [ ] 参数固定
- [ ] 明确停止条件
- [ ] 明确最大亏损
- [ ] 明确信号频率预期

## 每次归档策略前检查

- [ ] 为什么归档？
- [ ] 哪个 gate 失败？
- [ ] 是否保留结果？
- [ ] 是否写入 rejected conclusion？
- [ ] 是否从默认扫描中移除？

## 三个核心问题

每做一件事前问自己：

1. **这个结果能不能复现？**
2. **这个策略有没有通过门禁？**
3. **这个改动是不是让系统更简单、更可靠？**

如果答案不是三个"是"，就不要让它进入主线。
