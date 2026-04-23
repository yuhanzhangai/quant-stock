"""资金费率数据采集器。"""

from typing import Any

import polars as pl

from src.exchange.okx_client import OKXNativeClient
from src.ingestion.base import IngestorBase
from src.storage.parquet_writer import ParquetWriter
from src.storage.state_tracker import StateTracker


class FundingIngestor(IngestorBase):
    """资金费率采集器。

    使用 python-okx 客户端拉取永续合约资金费率历史。
    """

    def __init__(
        self,
        okx_client: OKXNativeClient,
        writer: ParquetWriter,
        state_tracker: StateTracker,
    ) -> None:
        super().__init__(writer, state_tracker, source_name="funding")
        self._client = okx_client

    async def fetch(
        self,
        symbol: str,
        timeframe: str = "",
        since: int | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """从 OKX 拉取资金费率历史。

        Args:
            symbol: 合约 ID，如 "BTC-USDT-SWAP"
            timeframe: 未使用（资金费率没有 timeframe 概念）
            since: 起始毫秒时间戳

        Returns:
            资金费率原始数据列表
        """
        records = await self._client.fetch_funding_rate_history_range(
            inst_id=symbol,
            since_ts=since,
        )
        return records

    def transform(self, raw_data: Any, symbol: str, **kwargs: Any) -> pl.DataFrame:
        """将资金费率原始数据转换为标准 DataFrame。"""
        if not raw_data:
            return pl.DataFrame()

        df = pl.DataFrame(
            {
                "funding_time": [int(r["fundingTime"]) for r in raw_data],
                "funding_rate": [float(r["fundingRate"]) for r in raw_data],
                "realized_rate": [
                    float(r["realizedRate"]) if r.get("realizedRate") else None for r in raw_data
                ],
                "symbol": [symbol] * len(raw_data),
            }
        )
        # 用 funding_time 作为 timestamp 给基类用
        df = df.with_columns(pl.col("funding_time").alias("timestamp"))
        return df

    def save(self, df: pl.DataFrame, symbol: str, timeframe: str) -> int:
        """保存资金费率数据。"""
        return self._writer.write_funding(df, symbol=symbol)
