"""安全闸测试:PAPER_ONLY 硬钉 + kill-switch。这是执行层最关键的测试。"""

import pytest

from src.execution import safety
from src.execution.safety import ExecutionHalted, KillSwitch, assert_paper_only


class TestPaperOnly:
    def test_paper_only_is_hard_pinned_true(self):
        """宪法红线:PAPER_ONLY 必须是 True。此测试失败 = 有人动了硬钉。"""
        assert safety.PAPER_ONLY is True

    def test_assert_paper_only_passes_in_clean_env(self, monkeypatch):
        for var in safety._LIVE_TRADING_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        assert_paper_only()  # 不抛即过

    @pytest.mark.parametrize("var", ["FT_LIVE_TRADING", "EXEC_LIVE", "EXEC_LIVE_TRADING", "LIVE_TRADING"])
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
    def test_live_trading_env_var_rejected(self, monkeypatch, var, value):
        monkeypatch.setenv(var, value)
        with pytest.raises(ExecutionHalted, match="真金"):
            assert_paper_only()

    def test_falsy_env_var_ok(self, monkeypatch):
        monkeypatch.setenv("FT_LIVE_TRADING", "0")
        assert_paper_only()


class TestKillFilePath:
    def test_default_kill_file_is_absolute_and_repo_anchored(self):
        """kill 路径必须锚定仓库根且不可配置 —— 与 exec_kill.sh/Makefile 永远同一文件。
        (A1+A2 审计确认过的 major:路径分叉 = 一键停静默失效)"""
        assert safety.DEFAULT_KILL_FILE.is_absolute()
        repo_root = safety.PROJECT_ROOT
        assert (repo_root / "CLAUDE.md").exists(), "PROJECT_ROOT 没指到仓库根"
        assert repo_root / "data" / "execution" / "KILL" == safety.DEFAULT_KILL_FILE

    def test_kill_file_not_configurable_via_exec_settings(self):
        """ExecSettings 里绝不允许出现 kill_file 字段(防 .env 覆盖造成路径分叉)。"""
        from src.execution.firstrade_agent.config import ExecSettings

        assert "kill_file" not in ExecSettings.model_fields


class TestKillSwitch:
    def test_not_engaged_check_passes(self, tmp_path):
        ks = KillSwitch(tmp_path / "KILL")
        assert not ks.engaged
        ks.check()  # 不抛即过

    def test_engage_then_check_halts(self, tmp_path):
        ks = KillSwitch(tmp_path / "KILL")
        ks.engage("测试触发")
        assert ks.engaged
        with pytest.raises(ExecutionHalted, match="kill-switch"):
            ks.check()

    def test_touch_file_externally_also_halts(self, tmp_path):
        """外部 touch 文件(如 scripts/exec_kill.sh)同样生效——不依赖 Python 进程。"""
        kill_file = tmp_path / "KILL"
        ks = KillSwitch(kill_file)
        kill_file.touch()
        with pytest.raises(ExecutionHalted):
            ks.check()

    def test_engage_appends_reasons(self, tmp_path):
        ks = KillSwitch(tmp_path / "KILL")
        ks.engage("原因一")
        ks.engage("原因二")
        lines = (tmp_path / "KILL").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert "原因一" in lines[0] and "原因二" in lines[1]

    def test_release_clears(self, tmp_path):
        ks = KillSwitch(tmp_path / "KILL")
        ks.engage("先停")
        ks.release()
        assert not ks.engaged
        ks.check()

    def test_check_also_enforces_paper_only(self, tmp_path, monkeypatch):
        """check() 同时校验 PAPER_ONLY 环境黑名单,不能只看 kill 文件。"""
        monkeypatch.setenv("LIVE_TRADING", "1")
        ks = KillSwitch(tmp_path / "KILL")
        with pytest.raises(ExecutionHalted, match="真金"):
            ks.check()
