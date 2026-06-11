"""Firstrade 模拟盘浏览器会话:Playwright + 登录态复用 + 全程安全闸/真人节奏。

所有页面动作(导航/点击/输入/读取)统一走本类的 guarded 原语,顺序固定:
kill-switch 检查 → 选择器核验(未核验拒跑)→ 人类节奏 → 动作 → 审计日志。

Playwright 延迟导入:研究环境不装 playwright 也能 import 本模块(只是不能 launch)。
凭据纪律:本类没有任何接收/存储密码的代码路径;首次登录由 operator 在有头浏览器
里人工完成(scripts/exec_login.py),此后只复用 storage_state 文件(已 gitignore)。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.execution.audit_log import AuditLog
from src.execution.firstrade_agent.config import ExecSettings, get_exec_settings
from src.execution.firstrade_agent.selectors import SelectorRegistry
from src.execution.human import HumanPacer
from src.execution.safety import ExecutionHalted, KillSwitch

# 凭据纪律(CLAUDE.md 红线 5)的代码化:疑似凭据输入框一律拒绝自动输入,
# 不管调用方是谁 —— 凭据只能由 operator 人工输入(scripts/exec_login.py)。
_CREDENTIAL_SELECTOR_PARTS = ("password", "passwd", "pin", "otp", "2fa", "mfa")


class FirstradeSession:
    """单账号、单浏览器会话。生命周期:launch → 若干 guarded 动作 → close。"""

    def __init__(
        self,
        settings: ExecSettings | None = None,
        killswitch: KillSwitch | None = None,
        pacer: HumanPacer | None = None,
        audit: AuditLog | None = None,
        registry: SelectorRegistry | None = None,
    ) -> None:
        self.settings = settings or get_exec_settings()
        # kill 文件路径不走配置:必须与 exec_kill.sh / Makefile 指向同一文件
        self.kill = killswitch or KillSwitch()
        self.pacer = pacer or HumanPacer(seed=self.settings.pacing_seed)
        self.audit = audit or AuditLog(self.settings.audit_log_file)
        self.registry = registry or SelectorRegistry.load(self.settings.selectors_file)
        self._pw: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None

    # ── 生命周期 ──────────────────────────────────────────────

    # 反检测:去掉 Playwright 默认会注入的自动化标志,并补一条压制旗标。
    # navigator.webdriver、--enable-automation 横幅都是 Firstrade 风控的识别点。
    _STEALTH_ARGS = ("--disable-blink-features=AutomationControlled",)
    _STEALTH_IGNORE_DEFAULT = ("--enable-automation",)
    # init script 兜底:即便上面漏网,也把 webdriver 抹成 undefined(只读 spoof,不碰凭据)
    _STEALTH_INIT_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

    def launch(self) -> None:
        self.kill.check()
        # 延迟导入:仅真正启动浏览器时才需要 playwright
        from playwright.sync_api import sync_playwright

        s = self.settings
        args = list(self._STEALTH_ARGS) if s.stealth else []
        ignore_default = list(self._STEALTH_IGNORE_DEFAULT) if s.stealth else []
        ctx_kwargs: dict[str, Any] = {}
        if s.user_agent:
            ctx_kwargs["user_agent"] = s.user_agent

        pw = sync_playwright().start()
        browser = None
        persistent = s.user_data_dir is not None
        try:
            # chromium_sandbox=True:Playwright 默认在未显式开 sandbox 时 push --no-sandbox
            # (chromiumSwitches:chromiumSandbox!==true → 注入),它既是自动化指纹信号、又触发
            # Chrome 顶部"不受支持的命令行标记"警告条。macOS 本地有头真 sandbox 工作正常,
            # 开它=根本不注入 --no-sandbox(比事后 ignore 干净)。stealth 关时回退 Playwright 默认。
            chromium_sandbox = s.stealth
            if persistent:
                # 持久化真实 profile:像一台日常浏览器,cookies/指纹跨次稳定(最强反检测)。
                # 此模式下 context 自带磁盘登录态,不再叠加 storage_state(profile 即登录态)。
                s.user_data_dir.mkdir(parents=True, exist_ok=True)
                context = pw.chromium.launch_persistent_context(
                    str(s.user_data_dir),
                    headless=s.headless,
                    channel=s.browser_channel,
                    args=args,
                    ignore_default_args=ignore_default,
                    chromium_sandbox=chromium_sandbox,
                    **ctx_kwargs,
                )
                auth_reused = any(s.user_data_dir.iterdir())
            else:
                browser = pw.chromium.launch(
                    headless=s.headless,
                    channel=s.browser_channel,
                    args=args,
                    ignore_default_args=ignore_default,
                    chromium_sandbox=chromium_sandbox,
                )
                auth_file = s.auth_state_file
                storage_state = str(auth_file) if auth_file.exists() else None
                context = browser.new_context(storage_state=storage_state, **ctx_kwargs)
                auth_reused = storage_state is not None
            if s.stealth:
                context.add_init_script(self._STEALTH_INIT_JS)
            context.set_default_timeout(s.default_timeout_ms)
            page = context.pages[0] if context.pages else context.new_page()
        except Exception:
            # 启动半途失败:把已起的进程收干净再上抛,不留孤儿 driver/browser
            if browser is not None:
                try:
                    browser.close()
                except Exception as e:
                    logger.warning("launch 失败清理 browser 异常(忽略): {}", e)
            try:
                pw.stop()
            except Exception as e:
                logger.warning("launch 失败清理 playwright 异常(忽略): {}", e)
            raise
        self._pw, self._browser, self._context, self._page = pw, browser, context, page
        self.audit.record(
            "session_launch",
            headless=s.headless,
            persistent=persistent,
            stealth=s.stealth,
            auth_state_reused=auth_reused,
            kill_file=str(self.kill.kill_file),  # 把实际监视的 kill 路径留痕,防路径分叉
        )

    def close(self) -> None:
        for obj, closer in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, closer)()
                except Exception as e:  # 收尾失败只记日志,不阻塞退出
                    logger.warning("session 关闭阶段异常(忽略): {}", e)
        self._page = self._context = self._browser = self._pw = None
        self.audit.record("session_close")

    def __enter__(self) -> FirstradeSession:
        self.launch()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _require_page(self) -> Any:
        """内部专用:返回 page 句柄。故意不做公有属性 —— 业务代码若直接拿
        page 就能绕过 kill 检查/选择器核验/节奏/审计,所有页面交互必须走
        下面的 guarded 原语。"""
        if self._page is None:
            raise RuntimeError("session 未 launch,先调用 launch()")
        return self._page

    def save_auth_state(self) -> None:
        """保存浏览器登录态到 gitignored 文件;只记路径,绝不记内容。"""
        self.kill.check()
        auth_file = self.settings.auth_state_file
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        if self._context is None:
            raise RuntimeError("session 未 launch,无登录态可存")
        self._context.storage_state(path=str(auth_file))
        self.audit.record("auth_state_saved", path=str(auth_file))

    # ── guarded 原语(所有页面交互必须走这里) ──────────────────
    # 顺序约定:入口 kill 检查 → 选择器核验 → 真人节奏等待 → **动作前再查一次 kill**
    # (节奏等待可达数秒,二次检查把 check-to-action 的 TOCTOU 窗口压到接近零)→ 动作 → 审计。

    def goto(self, url: str) -> None:
        self.kill.check()
        self._require_page().goto(url)
        self.pacer.after_navigation()
        # 审计只记不带 query/fragment 的 URL,防止未来某个带 token 的链接落进日志
        self.audit.record("goto", url=url.split("?", 1)[0].split("#", 1)[0])

    def click(self, selector_name: str) -> None:
        self.kill.check()
        sel = self.registry.require(selector_name)
        self.pacer.between_actions()
        self.kill.check()
        self._require_page().click(sel.css)
        self.audit.record("click", selector=selector_name)

    def type_human(self, selector_name: str, text: str, mask_in_audit: bool = False) -> None:
        """逐字符类人输入。疑似凭据输入框一律硬拒(凭据只人工输入)。
        mask_in_audit=True 时审计日志只记 ***。"""
        lowered = selector_name.lower()
        if any(part in lowered for part in _CREDENTIAL_SELECTOR_PARTS):
            raise ExecutionHalted(
                f"凭据纪律:拒绝自动输入疑似凭据字段 {selector_name!r}。"
                "凭据只能由 operator 人工输入(scripts/exec_login.py)。"
            )
        self.kill.check()
        sel = self.registry.require(selector_name)
        self.pacer.between_actions()
        self.kill.check()
        page = self._require_page()
        page.click(sel.css)  # 先聚焦,像人一样
        for ch, delay in zip(text, self.pacer.keystroke_delays(text), strict=True):
            self.kill.check()  # 长文本输入可持续数秒,逐键可停
            page.keyboard.type(ch)
            self.pacer.pause_keystroke(delay)
        self.audit.record(
            "type",
            selector=selector_name,
            text="***" if mask_in_audit else text,
        )

    def is_visible(self, selector_name: str) -> bool:
        self.kill.check()
        sel = self.registry.require(selector_name)
        return bool(self._require_page().locator(sel.css).first.is_visible())

    def read_text(self, selector_name: str) -> str:
        self.kill.check()
        sel = self.registry.require(selector_name)
        self.pacer.between_actions()
        self.kill.check()
        return str(self._require_page().locator(sel.css).first.inner_text())

    def read_table(self, selector_name: str) -> list[list[str]]:
        """读取表格为二维文本(按 tbody tr / 单元格)。"""
        self.kill.check()
        sel = self.registry.require(selector_name)
        self.pacer.between_actions()
        self.kill.check()
        rows = self._require_page().locator(f"{sel.css} tbody tr")
        out: list[list[str]] = []
        for i in range(rows.count()):
            cells = rows.nth(i).locator("td")
            out.append([str(cells.nth(j).inner_text()).strip() for j in range(cells.count())])
        return out

    def probe_css(self, css: str) -> dict[str, Any]:
        """核验专用只读探针:统计任意 css 命中数 + 首个命中元素摘要(P2 选择器核验)。

        故意不走 registry.require —— 本方法存在的意义就是核验尚未 verified 的
        选择器;只读 DOM(count/outerHTML),绝不点击/输入,kill 可停,留审计。"""
        self.kill.check()
        loc = self._require_page().locator(css)
        count = int(loc.count())
        sample = str(loc.first.evaluate("el => el.outerHTML"))[:200] if count else ""
        self.audit.record("probe_css", css=css, count=count)
        return {"css": css, "count": count, "sample": sample}

    def current_url(self) -> str:
        """只读:当前页面 URL(核验记录用;query/fragment 不落审计同 goto 约定)。"""
        self.kill.check()
        return str(self._require_page().url)

    def ensure_logged_in(self) -> bool:
        """检查登录态是否仍有效(依赖已核验的 logged_in_marker 选择器)。"""
        self.kill.check()
        sel = self.registry.require("logged_in_marker")
        visible = bool(self._require_page().locator(sel.css).first.is_visible())
        self.audit.record("login_check", logged_in=visible)
        return visible
