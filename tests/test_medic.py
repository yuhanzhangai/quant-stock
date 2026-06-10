"""Tests for scripts/medic.py — the watch-only team health daemon.

Fixtures below are real Claude Code TUI footers captured from the quant session
on 2026-06-10 (the day the stuck-input bug fired 4×), trimmed to test width.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "medic",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "medic.py"),
)
medic = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(medic)

DIV = "─" * 60

IDLE_EMPTY = f"""  现在待命,等 Lead 指令。
✻ Cooked for 1m 31s
{DIV} ultracode ─
❯
{DIV}
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
"""

STUCK_TEXT = "先把 gates/runner 的加密耦合点审查做了,产出 C4 适配清单"
IDLE_STUCK = f"""  现在待命,等 Lead 指令。
✻ Cooked for 1m 31s
{DIV} ultracode ─
❯ {STUCK_TEXT}
{DIV}
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
"""

BUSY_EMPTY = f"""· Proofing… (5m 44s · ↓ 18.4k tokens)
  ⎿  Tip: Use ctrl+v to paste images from your clipboard
{DIV} ultracode ─
❯
{DIV}
  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt
"""

WRAPPED_INPUT = f"""scrollback
{DIV} ultracode ─
❯ 第一行很长的未提交消息
  第二行是折行的延续
{DIV}
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
"""

MENU_OPEN = f"""scrollback
{DIV} ultracode ─
  1. Yes   2. No, tell me more
{DIV}
"""

# "esc to interrupt" leaked into scrollback (pane captured ANOTHER pane's
# footer while coordinating) but this pane's own footer says idle.
FOOTER_LEAK = f"""  ⎿  capture: "… esc to interrupt …"
{DIV} ultracode ─
❯
{DIV}
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
"""


# ----------------------------- extract_input ----------------------------- #

def test_extract_input_empty_box():
    assert medic.extract_input(IDLE_EMPTY) == ""


def test_extract_input_stuck_text():
    assert medic.extract_input(IDLE_STUCK) == STUCK_TEXT


def test_extract_input_busy_empty():
    assert medic.extract_input(BUSY_EMPTY) == ""


def test_extract_input_wrapped_joins_lines():
    assert medic.extract_input(WRAPPED_INPUT) == "第一行很长的未提交消息 第二行是折行的延续"


def test_extract_input_menu_returns_none():
    # a permission dialog / menu between the rules is NOT an input box -> unknown
    assert medic.extract_input(MENU_OPEN) is None


def test_extract_input_no_dividers_returns_none():
    assert medic.extract_input("plain text\nno dividers here") is None


def test_extract_input_gt_prompt_variant():
    text = f"{DIV}\n> hello world\n{DIV}\n  status"
    assert medic.extract_input(text) == "hello world"


# ------------------------------ parse_busy -------------------------------- #

def test_parse_busy_true_with_elapsed():
    busy, elapsed = medic.parse_busy(BUSY_EMPTY)
    assert busy is True
    assert elapsed == 5 * 60 + 44


def test_parse_busy_false_when_idle():
    busy, _ = medic.parse_busy(IDLE_STUCK)
    assert busy is False


def test_parse_busy_ignores_scrollback_leak():
    busy, _ = medic.parse_busy(FOOTER_LEAK)
    assert busy is False


# ------------------------------- classify --------------------------------- #

PANE = {"cmd": "claude.exe"}


def _cls(**kw):
    base = dict(pane=PANE, ctx_tok=None, busy=False, elapsed=None, frozen_for=0.0)
    base.update(kw)
    return medic.classify(**base)


def test_classify_stuck_input_fires():
    state, note = _cls(stuck_for=400.0, tx_stale=4000.0, stuck_excerpt="abc")
    assert "STUCK_INPUT" in state
    assert "abc" in note


def test_classify_stuck_suppressed_by_fresh_transcript():
    # Lead 2026-06-10: capture-pane 渲染可能滞后 — fresh transcript = agent is
    # actually processing, the visible "stuck" text is stale render / queue.
    state, _ = _cls(stuck_for=400.0, tx_stale=10.0)
    assert "STUCK_INPUT" not in state
    assert "IDLE" in state


def test_classify_stuck_not_flagged_while_busy():
    state, _ = _cls(busy=True, stuck_for=4000.0, tx_stale=10.0)
    assert "STUCK_INPUT" not in state


def test_classify_stuck_under_threshold_is_idle():
    state, _ = _cls(stuck_for=medic.STUCK_SEC - 1, tx_stale=4000.0)
    assert "IDLE" in state


def test_classify_stuck_outranks_context_overlay():
    # an undelivered directive never self-heals; CRIT_CONTEXT does (auto-compaction)
    state, _ = _cls(stuck_for=400.0, tx_stale=4000.0, ctx_tok=950_000)
    assert "STUCK_INPUT" in state


def test_classify_dead_when_shell():
    state, _ = medic.classify({"cmd": "zsh"}, None, False, None, 0.0)
    assert "DEAD" in state


def test_classify_wedged_needs_all_signals_silent():
    kw = dict(busy=True, frozen_for=700.0, tx_stale=700.0)
    state, _ = _cls(**kw)
    assert "WEDGED" in state
    # fresh transcript = progressing -> not wedged
    state, _ = _cls(**{**kw, "tx_stale": 5.0})
    assert "WEDGED" not in state
    # background shell in flight -> not wedged
    state, _ = _cls(**{**kw, "bg_shell": True})
    assert "WEDGED" not in state
    # medic's own pane exempt
    state, _ = _cls(**{**kw, "is_medic": True})
    assert "WEDGED" not in state


def test_classify_context_overlays():
    state, _ = _cls(ctx_tok=950_000)
    assert "CRIT_CONTEXT" in state
    state, _ = _cls(ctx_tok=850_000)
    assert "WARN_CONTEXT" in state


# --------------------------- auto_enter_eligible --------------------------- #
# operator via Lead 2026-06-10: one auto-Enter per non-forensic pane per hour;
# quant:0.0 (forensic) is never keyed; '/'/'!'-leading text never auto-submitted.

def test_auto_enter_happy_path():
    ok, _ = medic.auto_enter_eligible("quant:2.1", "Firstrade Exec", "继续待命", 0.0, 10_000.0)
    assert ok is True


def test_auto_enter_never_on_forensic_location():
    ok, why = medic.auto_enter_eligible("quant:0.0", "?", "确认:改单审", 0.0, 10_000.0)
    assert ok is False and "取证" in why


def test_auto_enter_never_on_audit_role():
    # belt-and-suspenders: even if windows renumber, the Audit role is never keyed
    ok, _ = medic.auto_enter_eligible("quant:5.0", "监工/Audit", "x", 0.0, 10_000.0)
    assert ok is False


def test_auto_enter_never_on_slash_or_bang():
    for txt in ("/clear", "!rm -rf /tmp/x"):
        ok, why = medic.auto_enter_eligible("quant:2.1", "Exec", txt, 0.0, 10_000.0)
        assert ok is False and "转人工" in why


def test_auto_enter_cooldown_one_per_hour():
    now = 10_000.0
    ok, why = medic.auto_enter_eligible("quant:2.1", "Exec", "msg", now - 3599.0, now)
    assert ok is False and "已自动补过" in why
    ok, _ = medic.auto_enter_eligible("quant:2.1", "Exec", "msg", now - 3601.0, now)
    assert ok is True


# --------------------------- check_tsay_failures --------------------------- #

@pytest.fixture()
def tsay_env(tmp_path, monkeypatch):
    log = tmp_path / "tsay_failures.log"
    pages: list[str] = []
    monkeypatch.setattr(medic, "TSAY_FAIL_LOG", str(log))
    monkeypatch.setattr(medic, "alert", pages.append)
    return log, pages


def test_tsay_missing_file_then_first_failure_pages(tsay_env):
    log, pages = tsay_env
    state: dict = {}
    medic.check_tsay_failures(state)            # baseline: no file -> pos 0
    assert pages == []
    log.write_text("2026-06-10 15:00:00  UNDELIVERED  target=quant:2.0  msg=x\n")
    medic.check_tsay_failures(state)
    assert len(pages) == 1 and "UNDELIVERED" in pages[0]


def test_tsay_preexisting_history_not_replayed(tsay_env):
    log, pages = tsay_env
    log.write_text("old failure line\n")
    state: dict = {}
    medic.check_tsay_failures(state)            # baseline at current size
    assert pages == []
    with open(log, "a") as f:
        f.write("new failure line\n")
    medic.check_tsay_failures(state)
    assert len(pages) == 1 and "new failure line" in pages[0]


def test_tsay_no_repage_without_growth(tsay_env):
    log, pages = tsay_env
    log.write_text("a\n")
    state: dict = {}
    medic.check_tsay_failures(state)
    medic.check_tsay_failures(state)
    medic.check_tsay_failures(state)
    assert pages == []


def test_tsay_storm_capped_at_three(tsay_env):
    log, pages = tsay_env
    state: dict = {}
    medic.check_tsay_failures(state)            # baseline pos 0 (no file)
    log.write_text("".join(f"fail {i}\n" for i in range(7)))
    medic.check_tsay_failures(state)
    assert len(pages) == 4                      # 3 lines + 1 "另有 N 条"
    assert "另有 4 条" in pages[-1]
