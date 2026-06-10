#!/usr/bin/env python3
"""Team medic daemon — bulletproof health monitor for the quant-stock tmux agent team.

Ported from stock-picker-mcp scripts/medic.py (operator-approved 2026-06-10),
adapted: SESSION="quant", quant roster, plus ONE NEW vital (#4 below).

WATCHES every claude pane in the `quant` tmux session and reports four vitals:
  1. 存活 (liveness)    — is claude still running in the pane, or did it drop to a shell?
  2. 卡死 (wedge)       — stuck generation: busy spinner up past a threshold with the
                          visible content frozen AND transcript silent = hung.
  3. 文本剩余 (context)  — current context tokens / 1M, read from the pane's transcript
                          .jsonl (last assistant `usage`: input + cache_read + cache_creation).
  4. 滞留输入 (stuck input) — NEW (Lead 2026-06-10, 当日已发作 4 次): pane is IDLE but its
                          TUI input box holds non-empty text that has sat unchanged past a
                          threshold = a send-keys message whose Enter never landed. The
                          recipient idles forever on an undelivered directive. Remediation:
                          one auto-Enter per pane per hour on non-forensic panes (operator
                          authorized 2026-06-10); quant:0.0 is alert-only forever.
  5. tsay 失败 (undelivered) — new lines in team/tsay_failures.log (appended by
                          scripts/tsay.sh when its delivery verification exhausts retries)
                          page the operator. Position-tracked, history never replayed.

CAVEAT (Lead 2026-06-10): capture-pane can render STALE — keys may have landed
while the visible screen lags. So (a) STUCK_INPUT additionally requires the
pane's transcript to be silent past the threshold (a fresh transcript = the
agent IS processing, the visible text is queue/stale-render, not stuck), and
(b) a WEDGED page is a SUSPICION: the Medic agent must force a repaint (test
char + immediate backspace, Lead-authorized) and re-capture before asking the
operator to act. The daemon itself still never sends keys.

WRITES  team/health.md (live dashboard) every poll, and APPENDS to
team/health_alerts.log + flashes the operator (tmux display-message) on any
transition INTO a bad state (WEDGED / DEAD / CRIT_CONTEXT / STUCK_INPUT).

HARD RULE (amended by operator via Lead, 2026-06-10): this daemon is watch-only —
no send-keys, no /clear, no restart — with EXACTLY ONE authorized exception:
on a confirmed STUCK_INPUT in a NON-forensic pane it may press Enter ONCE
(per pane per hour) to deliver the stuck message, logging every press to
health_alerts.log. Constraints that keep the exception narrow:
  - quant:0.0 (取证/forensic pane) is NEVER keyed — alert-only, route to operator;
  - text starting with '/' or '!' is never auto-submitted (could drive a slash
    popup / bash mode) — alert-only;
  - eligibility requires the full STUCK_INPUT confirmation (idle + text stable
    >= STUCK_SEC + transcript silent), never a raw guess.
Everything else stays pure reads, everything wrapped so one error can never
kill the loop. It has no LLM context, so it cannot itself wedge — that is the
point of the two-layer design.

Deliberate stack exception (flagged for Audit): stdlib-only, stdout via print in
--once mode. The watcher must keep running even when the uv venv / loguru is
broken — it guards the agents that would otherwise fix the venv. Logging goes to
team/health.md + team/health_alerts.log, not stdout.

Usage:
  python3 scripts/medic.py --once            # one pass, print table + write health.md
  python3 scripts/medic.py --loop 60         # daemon: poll every 60s
  python3 scripts/medic.py --loop 60 --stuck-sec 180   # tune stuck-input threshold
Map file team/medic_map.tsv (TAB: pane_id  sessionId  role) gives context accuracy;
missing/stale entries degrade context to '?', never to a wrong number.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TEAM = os.path.join(REPO, "team")
HEALTH_MD = os.path.join(TEAM, "health.md")
ALERTS_LOG = os.path.join(TEAM, "health_alerts.log")
MAP_TSV = os.path.join(TEAM, "medic_map.tsv")
MAP_SNAPSHOT = "/tmp/quant_medic_map.mine"  # Medic's last-written copy; daemon flags external edits
TSAY_FAIL_LOG = os.path.join(TEAM, "tsay_failures.log")  # appended by scripts/tsay.sh on failure

SESSION = "quant"            # only watch our team's session
CTX_WINDOW = 1_000_000       # Fable 5 [1m] (1M context)
CTX_WARN = 80                # % context -> warn
CTX_CRIT = 90                # % context -> critical (compaction imminent)
WEDGE_SEC = 600              # busy + ALL progress signals frozen this long => WEDGED.
                             # (agents do long legit turns; jsonl-mtime is the real guard, not the clock)
STUCK_SEC = 180              # idle + input box text unchanged this long => STUCK_INPUT
                             # (Lead/operator 2026-06-10: 空闲超 3 分钟即判滞留)
AUTO_ENTER_COOLDOWN = 3600   # auto-Enter remediation: at most once per pane per hour
FORENSIC_LOCS = ("quant:0.0",)          # forensic pane(s): alert-only, NEVER keyed
FORENSIC_ROLE_KEYS = ("Audit", "监工")  # belt-and-suspenders if windows ever renumber
CLAUDE_CMDS = ("claude", "claude.exe", "node")
# The Medic's OWN pane is exempt from WEDGED: its legitimate long working turns
# show the interrupt hint with a stable visible fingerprint, which the
# screen-diff heuristic misreads as "frozen" -> self-directed false alarms.
# It can't resuscitate itself anyway. DEAD/context/stuck-input still apply to it.
MEDIC_PANE = "%38"           # quant:4.0
MEDIC_ROLE_KEYS = ("医生", "Medic")
PROJ_ROOT = os.path.expanduser("~/.claude/projects")

# spinner / volatile lines to strip before fingerprinting the pane
_SPIN_GLYPHS = "✻✽✳✶✷✢✦✧·∗⏺⎿"
_TIME_RE = re.compile(r"\((?:(\d+)m)?\s*(?:(\d+)s)?\s*·")      # "(1m 56s · ..."
_ANYTIME_RE = re.compile(r"\((?:(\d+)m)?\s*(?:(\d+)s)?\b")     # fallback "(38s" / "(2m"
_BGSHELL_RE = re.compile(r"·\s*\d+\s*shell")                   # status line "· 1 shell" = bg work in flight
_DIVIDER_RE = re.compile(r"─{30,}")                            # TUI input-box top/bottom rule
_PROMPT_CHARS = ("❯", ">")                                     # input prompt glyph variants


def sh(cmd: list[str], timeout: float = 8.0) -> str:
    """Run a command, return stdout (''-safe). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:
        return ""


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------- discovery ----------------------------- #

def list_team_panes() -> list[dict]:
    """All panes in SESSION, with id/cmd/cwd/location."""
    fmt = "#{pane_id}|#{pane_current_command}|#{pane_current_path}|#{session_name}:#{window_index}.#{pane_index}"
    out = sh(["tmux", "list-panes", "-a", "-F", fmt])
    panes = []
    for ln in out.splitlines():
        parts = ln.split("|")
        if len(parts) != 4:
            continue
        pid, cmd, cwd, loc = parts
        if not loc.startswith(SESSION + ":"):
            continue
        panes.append({"pane": pid, "cmd": cmd, "cwd": cwd, "loc": loc})
    return panes


def load_map() -> dict:
    """pane_id -> {'session': sessionId, 'role': role}. Tolerant of a missing file."""
    m = {}
    try:
        with open(MAP_TSV, encoding="utf-8") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if not ln or ln.startswith("#"):
                    continue
                cols = ln.split("\t")
                if len(cols) >= 2:
                    m[cols[0]] = {"session": cols[1], "role": cols[2] if len(cols) > 2 else ""}
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return m


def proj_dir_for(cwd: str) -> str:
    """~/.claude/projects/<cwd-with-slashes-as-dashes>."""
    sanitized = cwd.replace("/", "-")
    return os.path.join(PROJ_ROOT, sanitized)


# ----------------------------- context ------------------------------- #

def context_tokens(cwd: str, session_id: str) -> int | None:
    """Last assistant usage in the pane's transcript -> current context tokens.

    Returns None if the transcript can't be found/read (shown as '?', never wrong).
    """
    if not session_id:
        return None
    path = os.path.join(proj_dir_for(cwd), session_id + ".jsonl")
    if not os.path.exists(path):
        return None
    last = None
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                # cheap prefilter: only parse lines that carry usage
                if '"usage"' not in ln:
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                u = (d.get("message") or {}).get("usage")
                if u and u.get("input_tokens") is not None:
                    last = u
    except Exception:
        return None
    if not last:
        return None
    return (last.get("input_tokens", 0)
            + last.get("cache_read_input_tokens", 0)
            + last.get("cache_creation_input_tokens", 0))


def transcript_stale_sec(cwd: str, session_id: str) -> float | None:
    """Seconds since the pane's transcript .jsonl was last written = progress signal.

    A live turn appends every tool step to its .jsonl, so a SMALL value means the
    agent is actively progressing (long-but-alive), not wedged. None if path unknown
    (degrades to screen-only judgement, never to a wrong number).
    """
    if not session_id:
        return None
    path = os.path.join(proj_dir_for(cwd), session_id + ".jsonl")
    try:
        return time.time() - os.path.getmtime(path)
    except Exception:
        return None


# ----------------------------- pane state ---------------------------- #

def capture(pane: str) -> str:
    return sh(["tmux", "capture-pane", "-t", pane, "-p"])


def _footer(text: str) -> str:
    """Last few non-empty lines = the TUI status bar region. Panes capture each
    OTHER's screens to coordinate, so status strings ("esc to interrupt", "N shell")
    leak into THIS pane's scrollback; only the real footer reflects this pane's own
    state."""
    lines = [l for l in text.splitlines() if l.strip()]
    return "\n".join(lines[-3:])


def parse_busy(text: str) -> tuple[bool, int | None]:
    """(busy, elapsed_seconds). Busy iff the TUI *footer* shows the interrupt hint."""
    busy = "esc to interrupt" in _footer(text)
    if not busy:
        return False, None
    m = _TIME_RE.search(text) or _ANYTIME_RE.search(text)
    if m:
        mm = int(m.group(1) or 0)
        ss = int(m.group(2) or 0)
        return True, mm * 60 + ss
    return True, None


def has_bg_shell(text: str) -> bool:
    """True if the TUI *footer* shows a background shell still running (work in flight)."""
    return bool(_BGSHELL_RE.search(_footer(text)))


def extract_input(text: str) -> str | None:
    """Text sitting in the pane's TUI input box, or None if unparseable.

    Real rendering (observed quant session, Claude Code TUI 2026-06):

        ──────────────────────────────────────── ultracode ─
        ❯ unsubmitted text, possibly wrapping
          onto continuation lines
        ─────────────────────────────────────────────────────
          ⏵⏵ bypass permissions on (shift+tab to cycle) · …

    The input area = lines between the LAST TWO horizontal rules; the first line
    carries the prompt glyph. Returns '' for a known-empty box, the joined text
    for content, and None when the region doesn't look like an input box (menu /
    permission dialog open, different TUI version) — None must NEVER alarm.
    """
    lines = text.splitlines()
    div = [i for i, ln in enumerate(lines) if _DIVIDER_RE.search(ln)]
    if len(div) < 2:
        return None
    top, bot = div[-2], div[-1]
    region = lines[top + 1:bot]
    if not region:
        return None
    first = region[0].lstrip()
    if not first.startswith(_PROMPT_CHARS):
        return None
    parts = [first[1:].strip()]
    parts += [ln.strip() for ln in region[1:]]
    return " ".join(p for p in parts if p).strip()


def fingerprint(text: str) -> str:
    """Hash the stable content of the pane (drop spinner/timer/status lines)."""
    keep = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if "esc to interrupt" in ln or "↓ to manage" in ln or "for agents" in ln:
            continue
        if s and s[0] in _SPIN_GLYPHS and ("·" in ln or "tokens" in ln or "…" in ln):
            continue  # spinner status line (volatile timer)
        if set(s) <= set("─-═ "):
            continue  # divider rules
        keep.append(ln.rstrip())
    return hashlib.md5("\n".join(keep).encode("utf-8", "ignore")).hexdigest()


def classify(pane: dict, ctx_tok: int | None, busy: bool, elapsed: int | None,
             frozen_for: float, is_medic: bool = False,
             tx_stale: float | None = None, bg_shell: bool = False,
             stuck_for: float = 0.0, stuck_excerpt: str = "") -> tuple[str, str]:
    """Return (state, note). state in DEAD/WEDGED/STUCK_INPUT/BUSY/IDLE plus context overlay."""
    cmd = pane["cmd"].lower()
    if not any(c in cmd for c in CLAUDE_CMDS):
        return "🔴 DEAD", "claude 已退出,pane 掉到 shell"

    ctx_pct = (ctx_tok / CTX_WINDOW * 100) if ctx_tok is not None else None

    # WEDGED = truly stuck, not merely slow. Idle never reaches here (no "esc to
    # interrupt"). To tell a long-but-ALIVE turn from a hung one, every progress
    # signal must be silent together:
    #   1. visible screen frozen >= WEDGE_SEC (fingerprint), AND
    #   2. transcript .jsonl silent >= WEDGE_SEC — a live multi-tool turn keeps
    #      appending steps, so a fresh mtime means it IS progressing, AND
    #   3. no background shell in flight.
    # Medic's own pane stays exempt (can't self-resuscitate anyway).
    progressing = (tx_stale is not None and tx_stale < WEDGE_SEC) or bg_shell
    if busy and frozen_for >= WEDGE_SEC and not is_medic and not progressing:
        mins = int(frozen_for // 60)
        return "🔴 WEDGED", f"生成中、画面+transcript 双冻结 {mins}m、无后台shell(疑卡死)"

    # STUCK_INPUT = idle pane with unsubmitted input-box text sitting unchanged.
    # Never self-heals (unlike CRIT_CONTEXT) — the recipient idles forever on an
    # undelivered directive, so it outranks the context overlays. Typing while
    # BUSY is normal queueing and is never flagged. A FRESH transcript also
    # suppresses it (Lead 2026-06-10: capture-pane 渲染可能滞后,按键已生效但画面
    # 未刷新 — transcript 在动说明 agent 正在消化消息,只是画面没跟上;真滞留 =
    # 画面文本与 transcript 同时静默过阈值,最坏延报一个 STUCK_SEC,绝不误报)。
    screen_maybe_stale = tx_stale is not None and tx_stale < STUCK_SEC
    if not busy and stuck_for >= STUCK_SEC and not screen_maybe_stale:
        mins = int(stuck_for // 60)
        return "🟡 STUCK_INPUT", f"输入框滞留未提交 {mins}m:「{stuck_excerpt}」(疑 Enter 未生效)"

    # context overlay (applies even when idle/busy)
    if ctx_pct is not None and ctx_pct >= CTX_CRIT:
        base = "BUSY" if busy else "IDLE"
        return "🔴 CRIT_CONTEXT", f"上下文 {ctx_pct:.0f}%≥{CTX_CRIT}%,压缩在即({base})"
    if ctx_pct is not None and ctx_pct >= CTX_WARN:
        base = "BUSY" if busy else "IDLE"
        return "🟡 WARN_CONTEXT", f"上下文 {ctx_pct:.0f}%≥{CTX_WARN}%({base})"

    if busy:
        e = f"{elapsed}s" if elapsed is not None else "?"
        return "🟢 BUSY", f"生成中 {e}"
    return "🟢 IDLE", "空闲待命"


# ----------------------------- reporting ----------------------------- #

def write_health(rows: list[dict], stamp: str, tsay_lines: list[str] | None = None) -> None:
    lines = [
        "# 团队健康体检表 (team/health.md)",
        "",
        f"_自动生成 · medic 守护脚本 · 最后体检: {stamp} · 每轮覆写_",
        "",
        "> 本脚本**只监护+报警**,唯一例外(operator 授权 2026-06-10):非取证 pane 确认 STUCK_INPUT 后",
        "> 自动补一次 Enter(1/pane/小时,每次落盘);**quant:0.0 取证 pane 永远只告警,绝不碰键**。",
        "> 其余处置由医生 agent 提请、**operator 批准**后执行(roster 红线:复苏经 operator 批准)。",
        "",
        "| pane | 角色 | 状态 | 文本剩余 | 备注 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        ctx = f"{r['ctx_pct']:.0f}% 用 / 剩 {100 - r['ctx_pct']:.0f}%" if r["ctx_pct"] is not None else "?"
        lines.append(f"| {r['pane']} | {r['role']} | {r['state']} | {ctx} | {r['note']} |")
    bad = [r for r in rows if r["bad"]]
    lines += ["", "## 🚑 当前告警", ""]
    if bad:
        for r in bad:
            cat = r["state"].split()[-1]
            if cat == "CRIT_CONTEXT":
                act = "提醒该员 /clear 或压缩;报 operator——守护脚本不代操作"
            elif cat == "DEAD":
                act = "需 operator 批准:进程级重启 + 按 roster/handoff 保身份复苏"
            elif cat == "STUCK_INPUT":
                act = ("非取证 pane:守护脚本自动补一次 Enter(operator 授权 2026-06-10,"
                       "1/pane/小时;/!开头转人工);quant:0.0 取证 pane 只告警转 operator")
            else:  # WEDGED
                act = ("医生先复核再报 operator:发测试字符强制重绘+立即退格(Lead 授权,"
                       "capture 渲染可能滞后),确认仍冻结才请批处置")
            lines.append(f"- **{r['pane']} {r['role']}** → {r['state']} — {r['note']}  ⟶ {act}")
    else:
        lines.append("- ✅ 全员健康,无需处置。")
    if tsay_lines:
        lines += ["", "## 📨 tsay 送达失败(最近 5 条,全量见 team/tsay_failures.log)", ""]
        lines += [f"- `{ln}`" for ln in tsay_lines]
    lines += [
        "",
        "## 复苏规程",
        "守护脚本纯只读;复苏/干预由医生 agent 按 team/roster.md 执行且需 operator 批准。",
        "WEDGED 复核(Lead 2026-06-10):医生发测试字符强制重绘+立即退格,再 capture 确认——capture 渲染可能滞后,按键已生效但画面未刷新。",
        "保身份复苏:从 roster 取 角色/cwd/handoff,让队员作为自己满血回来。",
        "",
    ]
    try:
        with open(HEALTH_MD, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


def alert(msg: str) -> None:
    try:
        with open(ALERTS_LOG, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()}  {msg}\n")
    except Exception:
        pass
    # non-intrusive flash on the operator's attached status line
    sh(["tmux", "display-message", "-t", SESSION, f"🚑 MEDIC: {msg}"])


def auto_enter_eligible(loc: str, role: str, inp: str,
                        last_enter: float, now: float) -> tuple[bool, str]:
    """Gate for the daemon's single authorized write (operator via Lead 2026-06-10).

    Returns (eligible, reason-if-not). Pure function so the policy is testable:
    forensic panes are never keyed; '/'/'!'-leading text could drive a slash
    popup or bash mode, so it is never auto-submitted; one press per pane per hour.
    """
    if loc in FORENSIC_LOCS or any(k in role for k in FORENSIC_ROLE_KEYS):
        return False, "取证 pane,只告警转 operator,绝不碰键"
    if inp.startswith(("/", "!")):
        return False, "文本以 / 或 ! 开头(恐驱动弹窗/bash 模式),转人工"
    if now - last_enter < AUTO_ENTER_COOLDOWN:
        return False, "本小时已自动补过一次,转人工"
    return True, ""


def read_tsay_tail(n: int = 5) -> list[str]:
    """Last n lines of the tsay failure log for the health.md dashboard ([] -safe)."""
    try:
        with open(TSAY_FAIL_LOG, encoding="utf-8") as f:
            return [ln.strip() for ln in f.read().splitlines() if ln.strip()][-n:]
    except Exception:
        return []


def check_tsay_failures(state: dict) -> None:
    """Page once per NEW line appended to tsay_failures.log since the last poll.

    Position-tracked in state; daemon (re)start never replays history (an existing
    file baselines at its current size; a missing file baselines at 0 so the very
    first failure after startup still pages). Truncation/rotation resets to 0.
    Capped at 3 pages per poll to avoid alert storms."""
    if not os.path.exists(TSAY_FAIL_LOG):
        state["tsay_pos"] = 0
        return
    try:
        size = os.path.getsize(TSAY_FAIL_LOG)
    except Exception:
        return
    pos = state.get("tsay_pos")
    if pos is None:            # first poll over a pre-existing log: skip history
        state["tsay_pos"] = size
        return
    if size < pos:             # truncated/rotated
        pos = 0
    if size == pos:
        return
    try:
        with open(TSAY_FAIL_LOG, encoding="utf-8") as f:
            f.seek(pos)
            new = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    except Exception:
        return
    state["tsay_pos"] = size
    for ln in new[:3]:
        alert(f"📨 tsay 送达失败 → {ln}")
    if len(new) > 3:
        alert(f"📨 tsay 送达失败另有 {len(new) - 3} 条(见 team/tsay_failures.log)")


def check_map_drift(state: dict) -> None:
    """Alert ONCE if medic_map.tsv diverges from the Medic's saved snapshot — i.e. a
    parallel actor edited the map. The Medic refreshes the snapshot after every legit
    edit, so any mismatch = an external write. Medic then reconciles (verify + correct
    the true session), NEVER write-wars with the other actor."""
    try:
        cur = open(MAP_TSV, encoding="utf-8").read()
        mine = open(MAP_SNAPSHOT, encoding="utf-8").read()
    except Exception:
        return  # no snapshot baseline yet, or unreadable -> nothing to compare
    if cur == mine:
        state["map_drift_h"] = None
        return
    h = hashlib.md5(cur.encode("utf-8", "ignore")).hexdigest()
    if state.get("map_drift_h") != h:          # de-dup: one page per distinct divergence
        alert("⚠️ medic_map.tsv 被非医生改写(疑并行 session)→ 医生核对真会话+纠正、勿对冲写")
        state["map_drift_h"] = h


# ----------------------------- main loop ----------------------------- #

def one_pass(state: dict) -> list[dict]:
    check_map_drift(state)          # flag if a parallel actor edited medic_map.tsv
    check_tsay_failures(state)      # page on new tsay delivery failures
    mapping = load_map()
    panes = list_team_panes()
    rows = []
    t = time.time()
    for p in panes:
        cmd = p["cmd"].lower()
        is_claude = any(c in cmd for c in CLAUDE_CMDS)
        info = mapping.get(p["pane"], {})
        role = info.get("role", "?")
        sess = info.get("session", "")

        text = capture(p["pane"]) if is_claude else ""
        busy, elapsed = parse_busy(text) if is_claude else (False, None)
        fp = fingerprint(text) if is_claude else ""

        st = state.setdefault(p["pane"], {"fp": fp, "fp_since": t, "last_state": ""})
        if fp != st["fp"]:
            st["fp"] = fp
            st["fp_since"] = t
        frozen_for = t - st["fp_since"]

        # stuck-input tracking: clock starts when non-empty input text is first
        # seen, resets whenever it changes or clears. '' / None never accumulate.
        inp = extract_input(text) if is_claude else None
        if inp:
            ih = hashlib.md5(inp.encode("utf-8", "ignore")).hexdigest()
            if st.get("in_h") != ih:
                st["in_h"] = ih
                st["in_since"] = t
            stuck_for = t - st["in_since"]
        else:
            st["in_h"] = None
            st["in_since"] = None
            stuck_for = 0.0
        excerpt = (inp[:24] + "…") if inp and len(inp) > 24 else (inp or "")

        ctx_tok = context_tokens(p["cwd"], sess) if is_claude else None
        ctx_pct = (ctx_tok / CTX_WINDOW * 100) if ctx_tok is not None else None

        tx_stale = transcript_stale_sec(p["cwd"], sess) if is_claude else None
        bg_shell = has_bg_shell(text) if is_claude else False
        is_medic = p["pane"] == MEDIC_PANE or any(k in role for k in MEDIC_ROLE_KEYS)
        state_str, note = classify(p, ctx_tok, busy, elapsed, frozen_for, is_medic,
                                   tx_stale, bg_shell, stuck_for, excerpt)

        # the ONE authorized write (operator via Lead 2026-06-10): deliver a stuck
        # message with a single Enter — non-forensic panes only, 1/pane/hour,
        # every press logged. Forensic / slash-bang / cooldown cases stay alert-only.
        if "STUCK_INPUT" in state_str:
            ok, why = auto_enter_eligible(p["loc"], role, inp or "",
                                          st.get("enter_ts", 0.0), t)
            if ok:
                sh(["tmux", "send-keys", "-t", p["pane"], "Enter"])
                st["enter_ts"] = t
                note += " → ⏎ 已自动补 Enter×1(operator 授权,1/pane/h)"
                alert(f"⏎ AUTO-ENTER {p['pane']} {role} — {note}")
            else:
                note += f" → {why}"

        bad = state_str.startswith("🔴") or "STUCK_INPUT" in state_str
        rows.append({"pane": p["pane"], "role": role, "state": state_str,
                     "ctx_pct": ctx_pct, "note": note, "bad": bad, "loc": p["loc"]})

        # De-noised alerting: one page per BAD EPISODE per category. Panes flap
        # within an episode (CRIT 90<->91%, BUSY<->IDLE, transient ctx-read gaps)
        # and CRIT_CONTEXT reliably self-heals via auto-compaction — re-paging each
        # tick is pure noise. Re-arm a category only after the pane returns to a
        # fully healthy 🟢 state (a genuinely new later episode pages once again).
        st.setdefault("alerted", set())
        if bad:
            cat = state_str.split()[-1]   # CRIT_CONTEXT / WEDGED / DEAD / STUCK_INPUT
            if cat not in st["alerted"]:
                alert(f"{p['pane']} {role} → {state_str} — {note}")
                st["alerted"].add(cat)
        elif state_str.startswith("🟢") and ctx_pct is not None and ctx_pct < CTX_WARN:
            st["alerted"].clear()         # re-arm ONLY on a CONFIRMED-healthy reading
            # (ctx known & <WARN). A transient ctx-read gap reads 🟢 / ctx=None and must
            # NOT clear the arm, else the next CRIT pass re-pages = the flap we killed.
        st["last_state"] = state_str
    return rows


def main() -> None:
    global STUCK_SEC
    ap = argparse.ArgumentParser(description="Team medic health daemon (watch-only).")
    ap.add_argument("--once", action="store_true", help="single pass + print table")
    ap.add_argument("--loop", type=float, default=0, help="daemon poll interval seconds (e.g. 60)")
    ap.add_argument("--stuck-sec", type=float, default=STUCK_SEC,
                    help=f"idle input-box text unchanged this long => STUCK_INPUT (default {STUCK_SEC})")
    args = ap.parse_args()
    STUCK_SEC = args.stuck_sec

    state: dict = {}
    interval = args.loop if args.loop else 60.0

    def run_and_report():
        rows = one_pass(state)
        stamp = now_iso()
        write_health(rows, stamp, read_tsay_tail())
        return rows, stamp

    if args.once or not args.loop:
        rows, stamp = run_and_report()
        print(f"== team health @ {stamp} ==")
        for r in rows:
            ctx = f"{r['ctx_pct']:.0f}%" if r["ctx_pct"] is not None else "?"
            print(f"  {r['pane']:<4} {r['loc']:<12} {r['state']:<16} ctx={ctx:<5} {r['role']} — {r['note']}")
        if not args.loop:
            return

    while True:
        with contextlib.suppress(Exception):
            run_and_report()  # bulletproof: never let one pass kill the daemon
        time.sleep(interval)


if __name__ == "__main__":
    main()
