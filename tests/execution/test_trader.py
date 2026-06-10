"""trader 安全行为测试:kill-switch 拦截 / 模拟盘环境核验 / dry_run 绝不提交 / 异常先停。

用 FakeSession(不依赖 playwright)验证 place_paper_order 的每一层安全闸。
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.execution.audit_log import AuditLog
from src.execution.firstrade_agent.models import OrderIntent, OrderStatus, OrderType, Side
from src.execution.firstrade_agent.selectors import UnverifiedSelectorError
from src.execution.firstrade_agent.trader import place_paper_order
from src.execution.human import HumanPacer
from src.execution.safety import KillSwitch


class FakeSession:
    """实现 trader 所需的 session 接口,记录每个动作,不碰浏览器。"""

    def __init__(self, tmp_path: Path, paper_marker_visible: bool = True):
        self.kill = KillSwitch(tmp_path / "KILL")
        self.audit = AuditLog(tmp_path / "audit.jsonl")
        self.pacer = HumanPacer(seed=1, sleep_fn=lambda _: None)
        self.paper_marker_visible = paper_marker_visible
        self.calls: list[tuple] = []
        self.fail_on: set[str] = set()  # 在这些选择器上抛异常,模拟页面故障
        self.unverified: set[str] = set()  # 模拟未核验选择器

    def _guard(self, name: str):
        self.kill.check()
        if name in self.unverified:
            raise UnverifiedSelectorError(f"选择器 {name!r} 未经实盘核验")
        if name in self.fail_on:
            raise TimeoutError(f"页面元素超时: {name}")

    def is_visible(self, name: str) -> bool:
        self._guard(name)
        self.calls.append(("is_visible", name))
        return self.paper_marker_visible

    def click(self, name: str) -> None:
        self._guard(name)
        self.calls.append(("click", name))

    def type_human(self, name: str, text: str, mask_in_audit: bool = False) -> None:
        self._guard(name)
        self.calls.append(("type", name, text))

    def read_text(self, name: str) -> str:
        self._guard(name)
        self.calls.append(("read_text", name))
        return "Order #12345 submitted (PAPER)"

    # 测试辅助
    def clicked(self, name: str) -> bool:
        return ("click", name) in self.calls

    def audit_events(self) -> list[str]:
        path = self.audit.path
        if not path.exists():
            return []
        return [json.loads(line)["event"] for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def intent() -> OrderIntent:
    return OrderIntent(symbol="NVDA", side=Side.BUY, qty=10, limit_price=Decimal("100.50"))


class TestSafetyGates:
    def test_kill_engaged_blocks_before_any_page_action(self, tmp_path, intent):
        session = FakeSession(tmp_path)
        session.kill.engage("测试先停")
        result = place_paper_order(session, intent)
        assert result.status is OrderStatus.HALTED
        assert session.calls == []  # 一个页面动作都没发生
        assert "order_halted" in session.audit_events()

    def test_missing_paper_marker_halts_and_engages_kill(self, tmp_path, intent):
        session = FakeSession(tmp_path, paper_marker_visible=False)
        result = place_paper_order(session, intent)
        assert result.status is OrderStatus.HALTED
        assert session.kill.engaged  # 出错先停:自动触发 kill-switch
        assert not session.clicked("order_submit_button")

    def test_unverified_selector_refuses_loudly(self, tmp_path, intent):
        """未核验选择器不是静默跳过,而是上抛 —— 不假装能用。"""
        session = FakeSession(tmp_path)
        session.unverified.add("paper_account_marker")
        with pytest.raises(UnverifiedSelectorError):
            place_paper_order(session, intent)

    def test_paper_marker_check_exception_engages_kill(self, tmp_path, intent):
        """环境核验本身抛页面异常(如 Target closed)也要先停,与其它阶段语义一致。"""
        session = FakeSession(tmp_path)
        session.fail_on.add("paper_account_marker")
        result = place_paper_order(session, intent)
        assert result.status is OrderStatus.HALTED
        assert session.kill.engaged
        assert not session.clicked("order_submit_button")
        assert "order_error" in session.audit_events()

    def test_form_exception_engages_kill_no_submit(self, tmp_path, intent):
        session = FakeSession(tmp_path)
        session.fail_on.add("order_qty_input")
        result = place_paper_order(session, intent, dry_run=False)
        assert result.status is OrderStatus.HALTED
        assert session.kill.engaged
        assert not session.clicked("order_submit_button")
        assert "order_error" in session.audit_events()

    def test_mid_flow_external_kill_halts_without_reengage(self, tmp_path, intent):
        """填表中途被外部一键停:立即停,且不重复 engage(kill 文件只有外部那一条)。"""
        session = FakeSession(tmp_path)
        original_click = session.click

        def click_then_kill(name: str) -> None:
            original_click(name)
            if name == "order_side_buy":  # 第一个动作后,外部触发 kill
                session.kill.kill_file.parent.mkdir(parents=True, exist_ok=True)
                session.kill.kill_file.write_text("external kill\n", encoding="utf-8")

        session.click = click_then_kill
        result = place_paper_order(session, intent, dry_run=False)
        assert result.status is OrderStatus.HALTED
        assert not session.clicked("order_submit_button")
        assert session.kill.kill_file.read_text(encoding="utf-8") == "external kill\n"


class TestDryRun:
    def test_dry_run_default_never_clicks_submit(self, tmp_path, intent):
        session = FakeSession(tmp_path)
        result = place_paper_order(session, intent)  # 默认 dry_run=True
        assert result.status is OrderStatus.DRY_RUN
        assert session.clicked("order_preview_button")  # 走到了预览
        assert not session.clicked("order_submit_button")  # 但绝没提交
        events = session.audit_events()
        assert "order_intent" in events and "order_dry_run" in events

    def test_limit_order_fills_price_field(self, tmp_path, intent):
        session = FakeSession(tmp_path)
        place_paper_order(session, intent)
        assert ("type", "order_limit_price_input", "100.50") in session.calls

    def test_market_order_skips_price_field(self, tmp_path):
        session = FakeSession(tmp_path)
        market = OrderIntent(symbol="AAPL", side=Side.SELL, qty=5, order_type=OrderType.MARKET)
        place_paper_order(session, market)
        assert not any(c[0] == "type" and c[1] == "order_limit_price_input" for c in session.calls)
        assert session.clicked("order_type_market")


class TestSubmit:
    def test_explicit_dry_run_false_submits_and_records(self, tmp_path, intent):
        session = FakeSession(tmp_path)
        result = place_paper_order(session, intent, dry_run=False)
        assert result.status is OrderStatus.SUBMITTED
        assert session.clicked("order_submit_button")
        assert "PAPER" in result.detail
        assert "order_submitted" in session.audit_events()

    def test_submit_exception_engages_kill(self, tmp_path, intent):
        session = FakeSession(tmp_path)
        session.fail_on.add("order_submit_button")
        result = place_paper_order(session, intent, dry_run=False)
        assert result.status is OrderStatus.HALTED
        assert session.kill.engaged
