"""python-okx 原生客户端封装，处理资金费率、持仓量等 OKX 特有数据。"""

import time
from typing import Any, Optional

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.exchange.rate_limiter import RateLimiterManager


class OKXNativeClient:
    """OKX 原生 API 客户端。

    用 python-okx 封装资金费率、持仓量等 CCXT 不覆盖的接口。
    集成限流器和 tenacity 重试。
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        use_simulated: bool = False,
    ) -> None:
        import okx.PublicData as PublicData

        flag = "1" if use_simulated else "0"

        self._public_api = PublicData.PublicAPI(
            api_key, api_secret, passphrase, flag=flag
        )
        self._rate_limiter = RateLimiterManager()
        logger.info("OKX 原生客户端初始化完成")

    def _check_response(self, response: dict[str, Any], context: str) -> list[dict[str, Any]]:
        """检查 API 响应并提取数据。

        Args:
            response: API 原始响应
            context: 调用上下文描述（用于日志）

        Returns:
            响应中的 data 列表

        Raises:
            RuntimeError: API 返回错误码
        """
        code = response.get("code", "-1")
        if code != "0":
            msg = response.get("msg", "未知错误")
            logger.error(f"{context} | 错误: [{code}] {msg}")
            raise RuntimeError(f"OKX API error [{code}]: {msg}")
        return response.get("data", [])

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, RuntimeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"重试 {retry_state.attempt_number}/5: {retry_state.outcome.exception()}"
        ),
    )
    async def fetch_funding_rate_history(
        self,
        inst_id: str,
        before: Optional[str] = None,
        after: Optional[str] = None,
        limit: str = "100",
    ) -> list[dict[str, Any]]:
        """获取资金费率历史。

        Args:
            inst_id: 合约 ID，如 "BTC-USDT-SWAP"
            before: 向旧翻页游标（返回比此时间戳更早的数据）
            after: 向新翻页游标
            limit: 每页数量（最多 100）

        Returns:
            资金费率数据列表
        """
        await self._rate_limiter.acquire("public/funding-rate-history")
        start = time.monotonic()

        params: dict[str, str] = {"instId": inst_id, "limit": limit}
        if before:
            params["before"] = before
        if after:
            params["after"] = after

        response = self._public_api.funding_rate_history(**params)

        elapsed = (time.monotonic() - start) * 1000
        data = self._check_response(response, f"funding_rate_history | {inst_id}")
        logger.debug(
            f"funding_rate_history | {inst_id} | 返回: {len(data)}条 | 耗时: {elapsed:.0f}ms"
        )
        return data

    async def fetch_funding_rate_history_range(
        self,
        inst_id: str,
        since_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """分页拉取资金费率历史全量数据。

        Args:
            inst_id: 合约 ID
            since_ts: 起始毫秒时间戳
            end_ts: 结束毫秒时间戳

        Returns:
            完整资金费率数据列表
        """
        all_data: list[dict[str, Any]] = []
        after_cursor: Optional[str] = None
        page = 0

        while True:
            page += 1
            records = await self.fetch_funding_rate_history(
                inst_id, after=after_cursor, limit="100"
            )

            if not records:
                break

            # 资金费数据按时间倒序返回，过滤范围
            filtered = records
            if since_ts is not None:
                filtered = [r for r in filtered if int(r["fundingTime"]) >= since_ts]
            if end_ts is not None:
                filtered = [r for r in filtered if int(r["fundingTime"]) <= end_ts]

            all_data.extend(filtered)

            # 检查是否已超出范围
            oldest_ts = int(records[-1]["fundingTime"])
            if since_ts is not None and oldest_ts <= since_ts:
                break

            if len(records) < 100:
                break

            # 下一页游标：最早一条的时间戳
            after_cursor = records[-1]["fundingTime"]

            if page % 5 == 0:
                logger.info(f"funding_rate_history_range | {inst_id} | 已拉取 {len(all_data)} 条（第 {page} 页）")

        logger.info(
            f"funding_rate_history_range 完成 | {inst_id} | 共 {len(all_data)} 条 | {page} 页"
        )
        return all_data

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, RuntimeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"重试 {retry_state.attempt_number}/5: {retry_state.outcome.exception()}"
        ),
    )
    async def fetch_open_interest(
        self,
        inst_type: str = "SWAP",
        inst_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """获取持仓量数据。

        Args:
            inst_type: 产品类型（SWAP, FUTURES, OPTION）
            inst_id: 合约 ID（可选，不传返回该类型全部）

        Returns:
            持仓量数据列表
        """
        await self._rate_limiter.acquire("public/open-interest")
        start = time.monotonic()

        params: dict[str, str] = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id

        response = self._public_api.get_open_interest(**params)

        elapsed = (time.monotonic() - start) * 1000
        data = self._check_response(response, f"open_interest | {inst_type} {inst_id or 'ALL'}")
        logger.debug(
            f"open_interest | {inst_type} {inst_id or 'ALL'} | "
            f"返回: {len(data)}条 | 耗时: {elapsed:.0f}ms"
        )
        return data

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, RuntimeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"重试 {retry_state.attempt_number}/5: {retry_state.outcome.exception()}"
        ),
    )
    async def fetch_current_funding_rate(self, inst_id: str) -> dict[str, Any]:
        """获取当前资金费率。

        Args:
            inst_id: 合约 ID，如 "BTC-USDT-SWAP"

        Returns:
            当前资金费率数据
        """
        await self._rate_limiter.acquire("default")
        start = time.monotonic()

        response = self._public_api.get_funding_rate(instId=inst_id)

        elapsed = (time.monotonic() - start) * 1000
        data = self._check_response(response, f"funding_rate | {inst_id}")
        logger.debug(f"funding_rate | {inst_id} | 耗时: {elapsed:.0f}ms")
        return data[0] if data else {}
