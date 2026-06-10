#!/usr/bin/env bash
# restart_team.sh — 一键重建 quant-stock 的 tmux 团队(5 窗口 / 8 pane)。
#
# 用法(★必须在【不属于 quant 会话】的另一个终端里跑,否则会把自己杀掉):
#   ~/quant-stock/scripts/restart_team.sh                 # 用 claude 默认(最新)模型
#   ~/quant-stock/scripts/restart_team.sh claude-opus-4-9 # 指定模型 id
#
# 做什么:杀旧 quant 会话 → 按 roster 重建窗口/pane → 每个 pane cd 到工作目录、起 claude、
# 发"恢复身份"init,让每个队员读 CLAUDE.md+roster+自己 handoff 满血回来。
# 与 stock-picker 的 `stock` 会话独立,可并存。
#
# ⚠ worktree 成员(Data/Strat/Exec)首次需先建 worktree(见下方注释),否则 cd 会失败。
set -u
MODEL="${1:-}"
S=quant
ROOT="$HOME/quant-stock"
CL="claude"; [ -n "$MODEL" ] && CL="claude --model $MODEL"

# 防自杀:别在 quant 会话里跑
if [ "${TMUX:-}" ] && tmux display-message -p '#S' 2>/dev/null | grep -qx "$S"; then
  echo "✋ 你正在 '$S' 会话里。请在另一个终端(不 attach quant)再跑,否则会杀掉自己。"; exit 1
fi

# 首次:为 worktree 成员建分支+worktree(已存在则跳过)
ensure_wt() {  # <dir> <branch>
  local d="$1" b="$2"
  [ -d "$d" ] && return 0
  git -C "$ROOT" worktree add -b "$b" "$d" 2>/dev/null || git -C "$ROOT" worktree add "$d" "$b" 2>/dev/null \
    || echo "⚠ 建 worktree $d ($b) 失败,先手动:git -C $ROOT worktree add -b $b $d"
}
ensure_wt "$HOME/quant-stock-data"  track/data
ensure_wt "$HOME/quant-stock-strat" track/strat
ensure_wt "$HOME/quant-stock-exec"  track/exec

echo "→ 杀旧 $S 会话(若存在)…"; tmux kill-session -t "$S" 2>/dev/null

spawn() {  # <target> <cwd> <init-text>
  local tgt="$1" cwd="$2" init="$3"
  tmux send-keys -t "$tgt" "cd '$cwd' && $CL" Enter
  sleep 4
  tmux load-buffer -b _ri - <<<"$init"
  tmux paste-buffer -t "$tgt" -b _ri -p 2>/dev/null
  sleep 0.4; tmux send-keys -t "$tgt" Enter
}

RI='读 CLAUDE.md + team/roster.md(你的职责+铁律)+ docs/MIGRATION_PLAN.md + 你的 handoff + 记忆,满血恢复为你自己,然后向 Lead(quant:0.1)报到待命。'

echo "→ 重建窗口/pane…"
# 窗口0: 0.0=监工, 0.1=Lead
tmux new-session -d -s "$S" -n core -c "$ROOT"
tmux split-window -t "$S:0" -c "$ROOT"
# 窗口1: 1.0=Data(worktree), 1.1=Strat(worktree)
tmux new-window -t "$S" -n research -c "$HOME/quant-stock-data"
tmux split-window -t "$S:1" -c "$HOME/quant-stock-strat"
# 窗口2: 2.0=Valid, 2.1=Exec(worktree)
tmux new-window -t "$S" -n exec -c "$ROOT"
tmux split-window -t "$S:2" -c "$HOME/quant-stock-exec"
# 窗口3: 3.0=Dash
tmux new-window -t "$S" -n dash -c "$ROOT"
# 窗口4: 4.0=Medic
tmux new-window -t "$S" -n medic -c "$ROOT"

echo "→ 各 pane 起 claude + 恢复身份…"
spawn "$S:0.0" "$ROOT"                  "你是【监工/Auditor(A1+A2 复核)】(quant:0.0)。$RI 把 CLAUDE.md 的 A1+A2 双 Agent 复核制度化:每个 commit/含结论回复,起两个独立子 agent 验证(可复现/无过拟合/无静默改参/无前视/事实准确),双 PASS 才放行。只审计,不碰 main。"
spawn "$S:0.1" "$ROOT"                  "你是【Lead 总指挥】(quant:0.1)。$RI 你是唯一动 main+canonical 的人;按 MIGRATION_PLAN 一次一个 checkpoint 驱动 研究→验证→门禁→Firstrade 模拟盘;红线:无真金下单代码、不改 frozen 基线、反过拟合不放水。"
spawn "$S:1.0" "$HOME/quant-stock-data"  "你是【数据工程 Data】(quant:1.0,track/data worktree)。$RI 美股数据采集(yfinance + 复用 ~/.stock-picker-mcp/prices.db)、universe(种子=诚实榜 PROVEN)、data quality gate。不碰 main/canonical。"
spawn "$S:1.1" "$HOME/quant-stock-strat" "你是【策略研究 Strat】(quant:1.1,track/strat worktree)。$RI 因子+策略迭代(MinSwing 风格,但要在股票上重验 edge、别假设迁得过来,没 edge 就明说)。不碰 main。"
spawn "$S:2.0" "$ROOT"                  "你是【回测验证 Valid】(quant:2.0)。$RI 验证管线、策略准入门禁、OOS、成本/滑点/压力测试、experiment ledger。不只看 Sharpe、必须可复现。"
spawn "$S:2.1" "$HOME/quant-stock-exec"  "你是【Firstrade Agent Exec】(quant:2.1,track/exec worktree)。$RI 建 Firstrade 模拟盘浏览器自动化 agent(Playwright,模拟真人节奏、单账号、PAPER_ONLY 硬钉、kill-switch、可一键停)。绝不写真金下单。不碰 main。"
spawn "$S:3.0" "$ROOT"                  "你是【面板/监控 Dash】(quant:3.0)。$RI Streamlit 研究控制台 + 模拟盘监控(盈亏/成交/agent 健康)。"
spawn "$S:4.0" "$ROOT"                  "你是【医生 Medic】(quant:4.0)。$RI 监护 pane 健康,复苏经 operator 批准(可复用 stock-picker 的 medic.py 思路)。"

echo "✅ quant 团队重建完成。attach:  tmux attach -t $S"
echo "   (与 stock-picker 的 'stock' 会话独立并存)"
