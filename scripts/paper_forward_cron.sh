#!/usr/bin/env bash
# paper_forward_cron.sh — launchd 每交易日盘后调用,跑一轮本地 paper 前向测试。
# 调度:工作日 14:30 PT(= 17:30 ET,美东收盘后),见
# ~/Library/LaunchAgents/com.quantstock.paperforward.plist。
# runner 自身对无数据日期 fail-closed(退出码 3),周末/假日触发也只是空转告警不造假。
set -u
cd "$HOME/quant-stock" || exit 1
export PATH="/opt/homebrew/bin:$PATH"   # launchd 环境 PATH 极简,uv 在此
mkdir -p logs
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') paper-forward 启动 ==="
  make paper-forward
  echo "=== 退出码 $? ==="
  echo
} >> logs/paper_forward_cron.log 2>&1
