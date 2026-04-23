"""异步 CCXT OKX 客户端封装，集成限流和重试。"""

import time
from typing import Any

import ccxt.async_support as ccxt
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.exchange.rate_limiter import RateLimiterManager


class CCXTClient:
    """CCXT 异步 OKX 客户端。

    提供 K线、Ticker、市场信息等常规数据的获取方法。
    集成限流器和 tenacity 重试（最多 5 次，指数退避）。
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        use_simulated: bool = False,
    ) -> None:
        config: dict[str, Any] = {
            "enableRateLimit": False,  # 我们自己管限流
            "options": {"defaultType": "spot"},
        }

        if api_key:
            config["apiKey"] = api_key
            config["secret"] = api_secret
            config["password"] = passphrase

        if use_simulated:
            config["sandbox"] = True

        self._exchange = ccxt.okx(config)
        self._rate_limiter = RateLimiterManager()
        logger.info("CCXT OKX 客户端初始化完成")

    async def close(self) -> None:
        """关闭客户端连接。"""
        await self._exchange.close()
        logger.debug("CCXT 客户端已关闭")

    async def __aenter__(self) -> "CCXTClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeNotAvailable)),
        before_sleep=lambda retry_state: logger.warning(
            f"重试 {retry_state.attempt_number}/5: {retry_state.outcome.exception()}"
        ),
    )
    async def fetch_tickers(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """获取 Ticker 数据。

        Args:
            symbols: 交易对列表，如 ["BTC/USDT", "ETH/USDT"]。None 获取全部。

        Returns:
            Ticker 字典，key 为交易对名称。
        """
        await self._rate_limiter.acquire("market/tickers")
        start = time.monotonic()

        result = await self._exchange.fetch_tickers(symbols)

        elapsed = (time.monotonic() - start) * 1000
        logger.debug(f"fetch_tickers | 数量: {len(result)} | 耗时: {elapsed:.0f}ms")
        return result

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeNotAvailable)),
        before_sleep=lambda retry_state: logger.warning(
            f"重试 {retry_state.attempt_number}/5: {retry_state.outcome.exception()}"
        ),
    )
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int = 100,
    ) -> list[list]:
        """获取单页 K 线数据。

        Args:
            symbol: 交易对，如 "BTC/USDT"
            timeframe: 时间周期，如 "1h", "4h", "1d"
            since: 起始毫秒时间戳
            limit: 数量（最多 100）

        Returns:
            K 线列表 [[timestamp, open, high, low, close, volume], ...]
        """
        await self._rate_limiter.acquire("market/candles")
        start = time.monotonic()

        result = await self._exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since, limit=limit
        )

        elapsed = (time.monotonic() - start) * 1000
        logger.debug(
            f"fetch_ohlcv | {symbol} {timeframe} | since: {since} | "
            f"返回: {len(result)}根 | 耗时: {elapsed:.0f}ms"
        )
        return result

    async def fetch_ohlcv_range(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        end: int | None = None,
        batch_size: int = 100,
    ) -> list[list]:
        """分页拉取大范围 K 线数据。

        Args:
            symbol: 交易对
            timeframe: 时间周期
            since: 起始毫秒时间戳
            end: 结束毫秒时间戳（None 表示到最新）
            batch_size: 每页数量

        Returns:
            完整 K 线列表
        """
        all_candles: list[list] = []
        current_since = since
        page = 0

        while True:
            page += 1
            candles = await self.fetch_ohlcv(
                symbol, timeframe=timeframe, since=current_since, limit=batch_size
            )

            if not candles:
                break

            # 过滤超出 end 的数据
            if end is not None:
                candles = [c for c in candles if c[0] <= end]

            all_candles.extend(candles)

            if len(candles) < batch_size:
                break

            # 下一页从最后一根 K 线的下一个时间点开始
            current_since = candles[-1][0] + 1

            if end is not None and current_since > end:
                break

            if page % 10 == 0:
                logger.info(
                    f"fetch_ohlcv_range | {symbol} {timeframe} | "
                    f"已拉取 {len(all_candles)} 根（第 {page} 页）"
                )

        logger.info(
            f"fetch_ohlcv_range 完成 | {symbol} {timeframe} | 共 {len(all_candles)} 根 | {page} 页"
        )
        return all_candles

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeNotAvailable)),
        before_sleep=lambda retry_state: logger.warning(
            f"重试 {retry_state.attempt_number}/5: {retry_state.outcome.exception()}"
        ),
    )
    async def fetch_markets(self) -> list[dict[str, Any]]:
        """获取所有市场信息。

        Returns:
            市场信息列表
        """
        await self._rate_limiter.acquire("default")
        start = time.monotonic()

        markets = await self._exchange.load_markets()

        elapsed = (time.monotonic() - start) * 1000
        logger.debug(f"fetch_markets | 数量: {len(markets)} | 耗时: {elapsed:.0f}ms")
        return list(markets.values())
