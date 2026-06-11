#!/usr/bin/env bash
# 每工作日 15:00 PT launchd 调用 → 生成当日 logs/daily_report.md
set -u
cd "$HOME/quant-stock" || exit 1
export PATH="/opt/homebrew/bin:$PATH"
uv run python scripts/daily_report.py >> logs/daily_report_cron.log 2>&1
