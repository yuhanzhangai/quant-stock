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

    def launch(self) -> None:
        self.kill.check()
        # 延迟导入:仅真正启动浏览器时才需要 playwright
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = None
        try:
            browser = pw.chromium.launch(
                headless=self.settings.headless,
                channel=self.settings.browser_channel,
            )
            auth_file = self.settings.auth_state_file
            storage_state = str(auth_file) if auth_file.exists() else None
            context = browser.new_context(storage_state=storage_state)
            context.set_default_timeout(self.settings.default_timeout_ms)
            page = context.new_page()
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
            headless=self.settings.headless,
            auth_state_reused=storage_state is not None,
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

    def ensure_logged_in(self) -> bool:
        """检查登录态是否仍有效(依赖已核验的 logged_in_marker 选择器)。"""
        self.kill.check()
        sel = self.registry.require("logged_in_marker")
        visible = bool(self._require_page().locator(sel.css).first.is_visible())
        self.audit.record("login_check", logged_in=visible)
        return visible
