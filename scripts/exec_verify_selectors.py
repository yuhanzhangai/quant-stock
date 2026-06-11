"""P2 选择器核验工具(交互式,只读):operator 在浏览器里翻页,我在终端逐组探测。

安全边界:
- 只读 DOM(count + outerHTML 摘要),**零点击零输入**;kill-switch 全程可停;
- 复用已保存登录态(.auth/firstrade_state.json,先跑 make exec-login);
- 有头模式:operator 全程看得见浏览器在哪个页面。

用法:uv run python scripts/exec_verify_selectors.py
命令:
  all / login / paper / account / order   按组探测当前页面上的注册选择器
  css <任意CSS>                            ad-hoc 探针(给占位猜错时找真选择器用)
  goto <url>                               导航(也可以 operator 直接在浏览器里点)
  quit                                     退出
探测结果只打印不改 YAML——verified: true 由人确认后手工逐项标注(留审计链)。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from src.execution.firstrade_agent.config import get_exec_settings
from src.execution.firstrade_agent.session import FirstradeSession
from src.execution.safety import ExecutionHalted

GROUPS: dict[str, tuple[str, ...]] = {
    "login": ("login_username", "login_password", "logged_in_marker"),
    "paper": ("paper_account_marker",),
    "account": ("positions_table", "account_cash", "account_buying_power"),
    "order": (
        "order_symbol_input",
        "order_qty_input",
        "order_side_buy",
        "order_side_sell",
        "order_type_limit",
        "order_type_market",
        "order_limit_price_input",
        "order_preview_button",
        "order_submit_button",
        "order_confirmation_text",
    ),
}
GROUPS["all"] = tuple(n for g in ("login", "paper", "account", "order") for n in GROUPS[g])


def verdict(count: int) -> str:
    if count == 1:
        return "✓ 唯一命中"
    if count == 0:
        return "✗ 未命中(占位猜错或不在本页)"
    return f"⚠ 命中 {count} 个(歧义,需收紧)"


def probe_group(session: FirstradeSession, group: str) -> None:
    logger.info("当前页面: {}", session.current_url())
    for name in GROUPS[group]:
        sel = session.registry.get(name)
        r = session.probe_css(sel.css)
        logger.info("{:<26} {:<28} {}", name, sel.css, verdict(r["count"]))
        if r["count"] >= 1:
            logger.debug("  样例: {}", r["sample"])


def main() -> int:
    settings = get_exec_settings()
    if settings.headless:
        logger.error("核验必须有头模式(operator 要看着页面),去掉 EXEC_HEADLESS=1")
        return 1
    if not settings.auth_state_file.exists():
        logger.error("登录态不存在,先跑 make exec-login: {}", settings.auth_state_file)
        return 1

    with FirstradeSession(settings=settings) as session:
        session.goto(settings.base_url)
        logger.info("已带登录态打开浏览器。operator 翻到目标页后,在这里敲组名探测。")
        while True:
            try:
                cmd = input("verify> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            try:
                if cmd in ("quit", "q", ""):
                    if cmd:
                        break
                elif cmd in GROUPS:
                    probe_group(session, cmd)
                elif cmd.startswith("css "):
                    r = session.probe_css(cmd[4:].strip())
                    logger.info("{} → {}", r["css"], verdict(r["count"]))
                    if r["count"]:
                        logger.info("  样例: {}", r["sample"])
                elif cmd.startswith("goto "):
                    session.goto(cmd[5:].strip())
                else:
                    logger.info("命令: all/login/paper/account/order | css <sel> | goto <url> | quit")
            except ExecutionHalted as e:
                logger.error("kill-switch 拦截,核验停止: {}", e)
                return 1
            except Exception as e:  # 探针失败不退出:换个选择器继续
                logger.warning("探测异常(继续): {}", e)
    logger.info("核验会话结束。确认无误的选择器请在 YAML 标 verified: true 后 commit。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
