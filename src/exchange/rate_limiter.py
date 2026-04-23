"""基于 aiolimiter 的按接口独立限流器。"""

from pathlib import Path
from typing import Optional

import yaml
from aiolimiter import AsyncLimiter
from loguru import logger


class RateLimiterManager:
    """管理多个接口的独立限流器。

    从 config/okx.yaml 读取限流配置，为每个接口创建独立的 AsyncLimiter。
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent.parent / "config" / "okx.yaml"

        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self._limiters: dict[str, AsyncLimiter] = {}
        rate_limits = config.get("rate_limits", {})

        for endpoint, limits in rate_limits.items():
            calls = limits["calls"]
            period = limits["period"]
            self._limiters[endpoint] = AsyncLimiter(calls, period)
            logger.debug(f"限流器注册: {endpoint} -> {calls}次/{period}秒")

    def get(self, endpoint: str) -> AsyncLimiter:
        """获取指定接口的限流器，未找到则使用默认配置。

        Args:
            endpoint: 接口路径，如 "market/candles"

        Returns:
            对应的 AsyncLimiter 实例
        """
        if endpoint in self._limiters:
            return self._limiters[endpoint]

        if "default" in self._limiters:
            logger.debug(f"接口 {endpoint} 使用默认限流配置")
            return self._limiters["default"]

        # 兜底：20次/2秒
        logger.warning(f"接口 {endpoint} 无限流配置，使用兜底 20次/2秒")
        fallback = AsyncLimiter(20, 2)
        self._limiters[endpoint] = fallback
        return fallback

    async def acquire(self, endpoint: str) -> None:
        """获取指定接口的限流许可。

        Args:
            endpoint: 接口路径
        """
        limiter = self.get(endpoint)
        await limiter.acquire()
