#!/usr/bin/env bash
# tsay.sh — 可靠的 pane 间消息直发工具(团队业务通信统一走这里,见 team/COMMS.md)。
#
# 用法: scripts/tsay.sh <tmux-target> <message>
#   例: scripts/tsay.sh quant:0.1 '[Exec→Lead][送审] paper agent v1 就绪 ACTION-REQUIRED'
#
# 实现: load-buffer(stdin 传 message,避免引号地狱)→ paste-buffer -p 到目标
#       → Enter → capture-pane 校验输入框已清空(❯ 提示行后无残留文本),
#       未清空则补发 Enter 重试(最多 3 次),仍失败 exit 1 并 stderr 告警。
set -u

if [ "$#" -lt 2 ]; then
  echo "用法: $0 <tmux-target> <message>" >&2; exit 2
fi
TARGET="$1"; shift
MSG="$*"

# 失败落盘(Lead 2026-06-10):medic 守护脚本 tail 此日志巡检告警。
# 固定指向主树 team/(worktree 副本也写同一处),可用 QUANT_TEAM_DIR 覆盖。
TSAY_FAIL_LOG="${QUANT_TEAM_DIR:-$HOME/quant-stock/team}/tsay_failures.log"
fail_log() {  # $1=原因码
  # 截断必须按【字符】不按字节(%.80s 是字节精度,中文 3 字节/字,切半个字 =
  # 非法 UTF-8 进日志,曾把 medic 守护的 tsay 体征整个打瞎);换行也压平,保持一行一条
  local head=${MSG//$'\n'/ }
  head=${head:0:80}
  printf '%s  %s  target=%s  msg=%s\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$TARGET" "$head" >> "$TSAY_FAIL_LOG" 2>/dev/null || true
}

# 目标 pane 必须存在,否则明确报错(capture-pane 对坏 session/window/pane 都会失败,
# display-message 会静默回退到当前 pane,不可用作存在性检查)
if ! tmux capture-pane -p -t "$TARGET" >/dev/null 2>&1; then
  echo "tsay: ✋ 目标 pane 不存在: $TARGET" >&2; fail_log NO_PANE; exit 1
fi

# 权限对话框探测:选项行渲染同款 ❯ 提示符("❯ 1. Yes"),盲发 Enter 会替对方
# 按掉对话框选项(=未授权替 agent 做决定)。检测到对话框一律不贴不按,转人工。
# 判据收紧(2026-06-10 首发即误报:消息正文引用"❯ 1. Yes"字样被全屏 grep 命中):
# 选择器必须在【行首】且只看屏幕【底部 15 行】——对话框渲染在底部,行首是其特征;
# 消息/滚屏里引用的同款字样在行中或上方,不再误伤。
dialog_open() {
  tmux capture-pane -p -t "$TARGET" 2>/dev/null | tail -n 15 | grep -qE '^[[:space:]]*❯ [0-9]+\. '
}
if dialog_open; then
  echo "tsay: ✋ $TARGET 有权限对话框打开,拒绝注入(恐替对方选选项),请人工处理" >&2
  fail_log DIALOG_OPEN; exit 1
fi

# stdin → buffer → 括号粘贴(-p),绕开 send-keys 的引号/特殊字符地狱
printf '%s' "$MSG" | tmux load-buffer -b _tsay -
tmux paste-buffer -p -d -b _tsay -t "$TARGET"
sleep 0.5
tmux send-keys -t "$TARGET" Enter

# ── 送达校验(三级判据,stock-picker relay.sh 经验:输入框清空≠送达)─────────
# CC v2.1.170 已知 bug:回合边界回车可能不被消费;更阴险的是"框清空但消息没进对话"
# 的静默丢失(2026-06-10 operator 确认行事故)。故:
#   DELIVERED = 滚屏(-J 合并折行)里能看到消息头  ← 终极判据
#   QUEUED    = 目标正忙,消息入队               ← 视为送达
#   CLEARED   = 仅输入框清空                     ← 不算!可能静默丢失,需重贴
# TODO: 升级为 transcript-mtime 判据(stock-picker %13 在做,出来后移植)
HEAD24="${MSG:0:24}"

queued() {
  local line
  line=$(tmux capture-pane -p -t "$TARGET" 2>/dev/null | grep '❯' | tail -n 1)
  case "${line#*❯}" in *"queued message"*) return 0 ;; esac
  return 1
}
input_cleared() {
  local line
  line=$(tmux capture-pane -p -t "$TARGET" 2>/dev/null | grep '❯' | tail -n 1)
  [ -n "$line" ] || return 1
  line=${line#*❯}
  [ -z "${line//[[:space:]]/}" ]
}
in_scrollback() {  # 输入框已空的前提下,滚屏匹配消息头 = 真进了对话
  tmux capture-pane -pJ -S -300 -t "$TARGET" 2>/dev/null | grep -qF -- "$HEAD24"
}

repaste() {
  printf '%s' "$MSG" | tmux load-buffer -b _tsay -
  tmux paste-buffer -p -d -b _tsay -t "$TARGET"
  sleep 0.5
  tmux send-keys -t "$TARGET" Enter
}

for attempt in 1 2 3 4; do
  sleep 1.5
  queued && exit 0                      # 入队 = 送达(目标空闲后自动处理)
  if input_cleared; then
    in_scrollback && exit 0             # 框空 + 滚屏可见 = DELIVERED
    # 框空但滚屏不见 = 静默丢失,重贴整条消息
    echo "tsay: 第 ${attempt} 次校验 $TARGET 框已空但消息未进对话(静默丢失),重贴…" >&2
    repaste
  else
    if dialog_open; then
      echo "tsay: ✋ $TARGET 中途弹出权限对话框,停止补 Enter(恐替对方选选项),请人工处理" >&2
      fail_log DIALOG_OPEN; exit 1
    fi
    echo "tsay: 第 ${attempt} 次校验 $TARGET 输入框未清空,补发 Enter…" >&2
    tmux send-keys -t "$TARGET" Enter
  fi
done
sleep 1.5
{ queued || { input_cleared && in_scrollback; }; } && exit 0

echo "tsay: ✋ 发送失败 — 重试 4 次后 $TARGET 仍无法确认送达(疑似输入通路死亡,需 Tier3 进程级处理),请人工检查" >&2
fail_log UNDELIVERED
exit 1
