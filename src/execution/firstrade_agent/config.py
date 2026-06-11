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

    # 反自动化检测(红线 6:模拟真人有封号灰区)。Playwright 默认带 --enable-automation,
    # 会置 navigator.webdriver=true + 显示"自动化软件控制"横幅 —— Firstrade 风控据此拒登
    # (operator 同账号在自家 Chrome 能登)。下面三道一起降指纹,**不碰凭据纪律**:
    #   1. persistent context:复用真实磁盘 profile(.auth/chrome_profile,gitignored),
    #      像一台日常用的浏览器而非每次全新无痕环境;留空则回退 storage_state 模式;
    #   2. 去掉 --enable-automation + 加 --disable-blink-features=AutomationControlled;
    #   3. init script 兜底抹掉 navigator.webdriver。
    user_data_dir: Path | None = PROJECT_ROOT / ".auth/chrome_profile"
    stealth: bool = True  # 关掉自动化指纹标志;排障时可 EXEC_STEALTH=0 对照
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
