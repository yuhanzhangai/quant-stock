"""首次人工登录 Firstrade,留存浏览器登录态(.auth/firstrade_state.json,已 gitignore)。

用法: uv run python scripts/exec_login.py

流程(凭据纪律:自动化代码绝不接触账号密码):
1. 打开有头浏览器,导航到 Firstrade 登录页;
2. operator **人工**输入账号密码、完成 2FA、进入模拟盘;
3. 回到本终端按回车 → 保存登录态文件,后续 agent 复用免登录;
4. 顺手核验页面:用 DevTools 确认 config/execution/firstrade_selectors.yaml
   里的选择器,确认一个标一个 verified: true(reader/trader 才会放行)。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from src.execution.firstrade_agent.config import get_exec_settings
from src.execution.firstrade_agent.session import FirstradeSession
from src.execution.safety import ExecutionHalted


def main() -> int:
    settings = get_exec_settings()
    if settings.headless:
        logger.error("首次登录必须有头模式(operator 要亲手输入凭据),请去掉 EXEC_HEADLESS=1")
        return 1

    session = FirstradeSession(settings=settings)
    try:
        session.launch()
        session.goto(settings.login_url)
        logger.info("浏览器已打开登录页。请人工完成登录(含 2FA),进入模拟盘账户页。")
        input("登录完成后,回到这里按回车保存登录态(Ctrl-C 放弃)… ")
        session.save_auth_state()
        logger.success("登录态已保存到 {}(gitignored,绝不提交)", settings.auth_state_file)
        logger.info(
            "下一步:用 DevTools 核验 {} 里的选择器,确认一个标一个 verified: true",
            settings.selectors_file,
        )
        return 0
    except KeyboardInterrupt:
        logger.warning("operator 取消,未保存登录态")
        return 1
    except ExecutionHalted as e:
        logger.error("安全闸拦截: {}", e)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
