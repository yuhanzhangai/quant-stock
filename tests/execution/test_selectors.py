"""选择器注册表测试:未核验选择器必须拒跑(诚实原则的代码化)。"""

from pathlib import Path

import pytest

from src.execution.firstrade_agent.selectors import SelectorRegistry, UnverifiedSelectorError

SAMPLE_YAML = """
selectors:
  verified_one:
    css: "#real"
    verified: true
    note: "已核验"
  unverified_one:
    css: "#guess"
    verified: false
    note: "占位"
  default_unverified:
    css: "#no-flag"
"""


@pytest.fixture
def registry(tmp_path: Path) -> SelectorRegistry:
    f = tmp_path / "selectors.yaml"
    f.write_text(SAMPLE_YAML, encoding="utf-8")
    return SelectorRegistry.load(f)


class TestSelectorRegistry:
    def test_get_known(self, registry):
        sel = registry.get("verified_one")
        assert sel.css == "#real" and sel.verified

    def test_get_unknown_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get("nope")

    def test_require_verified_ok(self, registry):
        assert registry.require("verified_one").css == "#real"

    def test_require_unverified_refuses(self, registry):
        with pytest.raises(UnverifiedSelectorError, match="未经实盘核验"):
            registry.require("unverified_one")

    def test_verified_defaults_to_false(self, registry):
        with pytest.raises(UnverifiedSelectorError):
            registry.require("default_unverified")

    def test_unverified_names(self, registry):
        assert registry.unverified_names() == ["default_unverified", "unverified_one"]

    def test_project_yaml_all_unverified_until_live_check(self):
        """仓库自带的选择器文件必须全部 verified: false,直到 operator 实盘核验。
        如果这个测试失败,说明有人未经核验就标了 true —— 审计必查。"""
        project_yaml = Path(__file__).resolve().parents[2] / "config/execution/firstrade_selectors.yaml"
        registry = SelectorRegistry.load(project_yaml)
        # 注:实盘核验后,operator 把 YAML 标 true 时应同步更新/删除本断言
        assert registry.unverified_names(), "选择器文件为空?"
        for name in ("paper_account_marker", "order_submit_button"):
            with pytest.raises(UnverifiedSelectorError):
                registry.require(name)
