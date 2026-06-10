"""loguru 日志地基 — stderr + 按日命名滚动文件，幂等。

所有入口脚本统一调用 setup_logging()（接线在后续转向手术中逐个完成）。
"""

import sys

from loguru import logger

from config.settings import get_settings

# 幂等标记：重复调用不叠 sink
_configured = False


def setup_logging(name: str | None = None) -> None:
    """配置全局 loguru：stderr + logs/{name}_YYYYMMDD.log（10 MB 滚动 / 保留 30 天）。"""
    global _configured
    if _configured:
        return
    settings = get_settings()
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)
    logger.add(
        f"logs/{name or 'quant'}_{{time:YYYYMMDD}}.log",
        rotation="10 MB",
        retention="30 days",
        level=settings.log_level,
    )
    _configured = True
