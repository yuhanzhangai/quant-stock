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

    # 反自动化检测(红线 6:模拟真人有封号灰区)。Playwright 默认带 --enable-automation /
    # --no-sandbox,会置 navigator.webdriver=true + 自动化横幅 + 警告条 —— Firstrade 风控据此
    # 拒登。更强一层:全新空 profile 也会被拦,只有**养熟的信任 profile**能过(operator 实测)。
    # 故路线 = 专用隔离 profile,operator 先用真实 Chrome 在该目录手动登一次养熟,Playwright 再接管。
    #
    # EXEC_CHROME_PROFILE_DIR 指向专用 profile 目录(**绝不指 operator 主 profile**——
    # 那会把其全部登录暴露给无人值守自动化,P3 风险红线)。默认 .auth/chrome_profile(gitignored)。
    # 留空(EXEC_CHROME_PROFILE_DIR="")则回退一次性 storage_state 模式(不推荐,易被风控拦)。
    chrome_profile_dir: Path | None = PROJECT_ROOT / ".auth/chrome_profile"
    stealth: bool = True  # 关掉自动化指纹标志(enable-automation/no-sandbox/webdriver);EXEC_STEALTH=0 对照
    user_agent: str | None = None  # 留空 = 用 channel=chrome 自带的真实 UA(已是真 Chrome)

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
