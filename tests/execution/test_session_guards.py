"""session 安全闸顺序测试:kill-switch 必须在任何页面访问之前生效。

不依赖 playwright:未 launch 的 session 没有 page,若 kill 检查不是第一步,
这些调用会抛 RuntimeError(page 未初始化)而不是 ExecutionHalted。
"""

from pathlib import Path

import pytest

from src.execution.audit_log import AuditLog
from src.execution.firstrade_agent.config import ExecSettings
from src.execution.firstrade_agent.selectors import Selector, SelectorRegistry
from src.execution.firstrade_agent.session import FirstradeSession
from src.execution.human import HumanPacer
from src.execution.safety import ExecutionHalted, KillSwitch


def make_session(tmp_path: Path, sleep_fn=lambda _: None) -> FirstradeSession:
    settings = ExecSettings(
        audit_log_file=tmp_path / "audit.jsonl",
        auth_state_file=tmp_path / "auth.json",
    )
    registry = SelectorRegistry(
        {
            "logged_in_marker": Selector(name="logged_in_marker", css="#x", verified=True),
            "login_password": Selector(name="login_password", css="#pw", verified=True),
        }
    )
    return FirstradeSession(
        settings=settings,
        killswitch=KillSwitch(tmp_path / "KILL"),
        pacer=HumanPacer(seed=1, sleep_fn=sleep_fn),
        audit=AuditLog(settings.audit_log_file),
        registry=registry,
    )


@pytest.fixture
def session(tmp_path: Path) -> FirstradeSession:
    return make_session(tmp_path)


class TestKillBeforePageAccess:
    """kill 已触发时,所有 guarded 原语必须抛 ExecutionHalted,而非碰 page。"""

    def test_all_primitives_halt_when_killed(self, session):
        session.kill.engage("测试")
        for call in (
            lambda: session.goto("https://example.com"),
            lambda: session.click("logged_in_marker"),
            lambda: session.type_human("logged_in_marker", "x"),
            lambda: session.is_visible("logged_in_marker"),
            lambda: session.read_text("logged_in_marker"),
            lambda: session.read_table("logged_in_marker"),
            lambda: session.ensure_logged_in(),
            lambda: session.save_auth_state(),
            lambda: session.launch(),
        ):
            with pytest.raises(ExecutionHalted):
                call()

    def test_page_access_without_launch_is_explicit_error(self, session):
        with pytest.raises(RuntimeError, match="未 launch"):
            session._require_page()

    def test_no_public_page_escape_hatch(self, session):
        """红队发现的纵深缺口:公有 page 访问器能绕过全部安全闸,必须不存在。"""
        assert not hasattr(session, "page")


class TestCredentialDiscipline:
    def test_type_human_refuses_credential_like_selectors(self, session):
        """凭据字段一律拒绝自动输入,即使选择器已核验(凭据只人工)。"""
        with pytest.raises(ExecutionHalted, match="凭据"):
            session.type_human("login_password", "hunter2")

    def test_refusal_happens_before_kill_or_page(self, tmp_path):
        """凭据拒绝是第一道检查:未 launch 也不会碰 page,直接 ExecutionHalted。"""
        s = make_session(tmp_path)
        with pytest.raises(ExecutionHalted):
            s.type_human("login_password", "x")
        assert not (tmp_path / "audit.jsonl").exists()  # 连审计都没到,更没碰页面


class TestKillDuringPacing:
    def test_kill_engaged_during_pacing_sleep_stops_before_action(self, tmp_path):
        """TOCTOU 收口:节奏等待期间外部触发 kill,动作前的二次检查必须拦住。
        (page 未 launch:若二次检查缺失,会先撞上 RuntimeError 而非 ExecutionHalted)"""
        holder: dict = {}

        def sleep_engages_kill(_: float) -> None:
            holder["session"].kill.engage("等待期间外部一键停")

        s = make_session(tmp_path, sleep_fn=sleep_engages_kill)
        holder["session"] = s
        with pytest.raises(ExecutionHalted, match="kill-switch"):
            s.click("logged_in_marker")
