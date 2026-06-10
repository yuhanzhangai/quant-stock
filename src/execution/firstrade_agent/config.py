"""执行层配置(pydantic-settings,前缀 EXEC_)。

注意:这里没有、也永远不会有"真金/模拟"切换开关 —— PAPER_ONLY 硬钉在
src/execution/safety.py,不属于配置。凭据也不在配置里:首次登录由 operator
人工输入(scripts/exec_login.py),之后复用浏览器登录态文件(已 gitignore)。

kill 文件路径同样**故意不在这里**:它是停机机制的一部分,必须与
scripts/exec_kill.sh / Makefile 永远指向同一文件,不允许被 .env 覆盖而分叉
(见 safety.DEFAULT_KILL_FILE)。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from src.execution.safety import PROJECT_ROOT


class ExecSettings(BaseSettings):
    """Firstrade agent 执行配置。全部可由 .env 以 EXEC_ 前缀覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="EXEC_",
        extra="ignore",
    )

    # 浏览器
    headless: bool = False  # 默认有头:便于 operator 旁观/接管,也更接近真人环境
    browser_channel: str = "chrome"  # 用系统 Chrome 而非裸 Chromium,降低指纹怪异度
    auth_state_file: Path = PROJECT_ROOT / ".auth/firstrade_state.json"  # 登录态(gitignored)
    default_timeout_ms: int = 30_000

    # Firstrade 入口(只放公开 URL;具体页面路径在选择器 YAML 里随核验一起维护)
    base_url: str = "https://www.firstrade.com"
    login_url: str = "https://invest.firstrade.com/cgi-bin/login"

    # 审计日志(锚定仓库根,不随 cwd 漂移)
    audit_log_file: Path = PROJECT_ROOT / "data/execution/audit.jsonl"

    # 选择器注册表
    selectors_file: Path = PROJECT_ROOT / "config/execution/firstrade_selectors.yaml"

    # 人类节奏:可注入固定种子用于排查复现;生产留空 = 真随机
    pacing_seed: int | None = None


def get_exec_settings() -> ExecSettings:
    return ExecSettings()
