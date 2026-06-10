"""限流器测试。"""

import asyncio

import pytest

from src.exchange.rate_limiter import RateLimiterManager


@pytest.fixture
def limiter() -> RateLimiterManager:
    return RateLimiterManager()


class TestRateLimiterManager:
    def test_get_known_endpoint(self, limiter: RateLimiterManager) -> None:
        """已配置的接口应返回对应限流器。"""
        lim = limiter.get("market/candles")
        assert lim is not None
        assert lim.max_rate == 40

    def test_get_default_endpoint(self, limiter: RateLimiterManager) -> None:
        """未配置的接口应使用默认限流器。"""
        lim = limiter.get("some/unknown/endpoint")
        default = limiter.get("default")
        assert lim is default

    def test_get_same_instance(self, limiter: RateLimiterManager) -> None:
        """同一接口多次获取应返回同一实例。"""
        lim1 = limiter.get("market/candles")
        lim2 = limiter.get("market/candles")
        assert lim1 is lim2

    @pytest.mark.asyncio
    async def test_acquire(self, limiter: RateLimiterManager) -> None:
        """acquire 应该不会阻塞（在限额内）。"""
        await limiter.acquire("market/candles")
        # 如果能走到这里说明 acquire 成功

    @pytest.mark.asyncio
    async def test_acquire_multiple(self, limiter: RateLimiterManager) -> None:
        """多次 acquire 在限额内不应阻塞。"""
        tasks = [limiter.acquire("market/tickers") for _ in range(5)]
        await asyncio.gather(*tasks)
