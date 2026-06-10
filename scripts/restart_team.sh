#!/usr/bin/env bash
# restart_team.sh — 一键重建 quant-stock 的 tmux 团队(5 窗口 / 8 pane)。
#
# 用法(★必须在【不属于 quant 会话】的另一个终端里跑,否则会把自己杀掉):
#   ~/quant-stock/scripts/restart_team.sh                 # 用 claude 默认(最新)模型
#   ~/quant-stock/scripts/restart_team.sh claude-opus-4-9 # 指定模型 id
#
# 做什么:杀旧 quant 会话 → 按 roster 重建窗口/pane → 每个 pane cd 到工作目录、起 claude、
# 发"恢复身份"init,让每个队员读 CLAUDE.md+roster 自己条目+AUDIT_PROTOCOL+自己 handoff 满血回来。
# 与 stock-picker 的 `stock` 会话独立,可并存。
#
# ★ effort 分层(operator 拍板 2026-06-10 v2:烧 token 没关系,慢才是问题——
#   产代码的开 ultra 全力烧;交互链路上的(协调/审裁/告警)要快,降档):
#     默认全员 high(operator 2026-06-10 v3:常驻 ultra 太烧,重活时对单个 pane 临时升 ultra)
#     high = 除 Medic 外全部;ultra 档保留在 spawn() 可用,仅按需单点启用
#     low   = Medic(4.0)               ← 告警循环要秒回
#
# ★ init 文本只给"你是谁 + 去哪读"。角色职责细节的单一事实源是 team/roster.md,
#   不要在本脚本里复制职责描述(防漂移)。审计制度按 team/AUDIT_PROTOCOL.md(单 Agent 审核)。
#
# ⚠ worktree 成员(Data/Strat/Exec)首次需先建 worktree(见下方注释),否则 cd 会失败。
set -u
MODEL="${1:-}"
S=quant
ROOT="$HOME/quant-stock"
CLBASE="claude"; [ -n "$MODEL" ] && CLBASE="claude --model $MODEL"

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

spawn() {  # <target> <cwd> <effort:low|high|ultra> <init-text>
  local tgt="$1" cwd="$2" effort="$3" init="$4"
  local cmd
  if [ "$effort" = "ultra" ]; then
    # 必须同时给 effortLevel:xhigh——只给 ultracode:true 时若全局 effortLevel 较低,
    # ultracode 会因"需 xhigh"条件不满足被静默忽略(2026-06-10 实测踩坑)
    cmd="$CLBASE --settings '{\"ultracode\":true,\"effortLevel\":\"xhigh\"}'"
  else
    cmd="$CLBASE --settings '{\"ultracode\":false}' --effort $effort"
  fi
  tmux send-keys -t "$tgt" "cd '$cwd' && $cmd" Enter
  # 等 claude 的 ❯ 输入框就绪再发 init(最多 30s)——过早 paste 会把 init 当 shell 命令执行!
  local i
  for i in $(seq 1 30); do
    tmux capture-pane -p -t "$tgt" 2>/dev/null | grep -q '❯' && break
    sleep 1
  done
  # 经 tsay.sh 发 init(送达校验+重试,治回车丢失);tsay 缺失时回退裸 paste
  if [ -x "$ROOT/scripts/tsay.sh" ]; then
    "$ROOT/scripts/tsay.sh" "$tgt" "$init" || echo "⚠ $tgt init 送达失败,请人工检查该 pane"
  else
    tmux load-buffer -b _ri - <<<"$init"
    tmux paste-buffer -t "$tgt" -b _ri -p 2>/dev/null
    sleep 0.4; tmux send-keys -t "$tgt" Enter
  fi
}

RI='读 CLAUDE.md + team/roster.md 中你的条目 + team/AUDIT_PROTOCOL.md + 你的 handoff + 记忆,满血恢复为你自己,向 Lead(quant:0.1)报到待命。'

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
spawn "$S:0.0" "$ROOT"                   high   "你是【机动/Review(审核制度已废止,按需接 Lead 指派)】(quant:0.0)。$RI"
spawn "$S:0.1" "$ROOT"                   high   "你是【Lead 总指挥】(quant:0.1)。$RI"
spawn "$S:1.0" "$HOME/quant-stock-data"  high   "你是【数据工程 Data】(quant:1.0)。$RI"
spawn "$S:1.1" "$HOME/quant-stock-strat" high   "你是【策略研究 Strat】(quant:1.1)。$RI"
spawn "$S:2.0" "$ROOT"                   high   "你是【回测验证 Valid】(quant:2.0)。$RI"
spawn "$S:2.1" "$HOME/quant-stock-exec"  high    "你是【Firstrade Agent Exec】(quant:2.1)。$RI"
spawn "$S:3.0" "$ROOT"                   high   "你是【面板/监控 Dash】(quant:3.0)。$RI"
spawn "$S:4.0" "$ROOT"                   low    "你是【医生 Medic】(quant:4.0)。$RI"

echo "✅ quant 团队重建完成。attach:  tmux attach -t $S"
echo "   (与 stock-picker 的 'stock' 会话独立并存)"
