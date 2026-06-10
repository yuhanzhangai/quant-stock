#!/usr/bin/env bash
# 执行层一键停:触发文件式 kill-switch。
# 文件存在 => 所有执行动作(导航/点击/输入/下单)在下一个动作前抛 ExecutionHalted。
# 解除(仅人工):make exec-resume 或 rm data/execution/KILL
# ⚠️ 路径必须与 src/execution/safety.py 的 DEFAULT_KILL_FILE 一致(仓库根/data/execution/KILL)。
#    kill 路径故意不可配置,防止本脚本与 agent 监视的文件分叉导致停机失效。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/execution
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) manual kill via scripts/exec_kill.sh" >> data/execution/KILL
echo "✋ kill-switch ENGAGED: data/execution/KILL(解除需人工 make exec-resume)"
