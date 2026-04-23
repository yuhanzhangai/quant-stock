"""OHLCV K 线数据采集器。"""

from typing import Any, Optional

import polars as pl
from loguru import logger

from src.exchange.ccxt_client import CCXTClient
from src.ingestion.base import IngestorBase
from src.storage.parquet_writer import ParquetWriter
from src.storage.state_tracker import StateTracker


class OHLCVIngestor(IngestorBase):
    """OHLCV K 线采集器。

    使用 CCXT 客户端从 OKX 拉取 K 线数据，支持增量更新。
    """

    def __init__(
        self,
        ccxt_client: CCXTClient,
        writer: ParquetWriter,
        state_tracker: StateTracker,
        market_type: str = "spot",
    ) -> None:
        super().__init__(writer, state_tracker, source_name="ohlcv")
        self._client = ccxt_client
        self._market_type = market_type

    def _symbol_to_ccxt(self, symbol: str) -> str:
        """将 OKX 格式转为 CCXT 格式。BTC-USDT -> BTC/USDT"""
        return symbol.replace("-", "/")

    async def fetch(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        **kwargs: Any,
    ) -> list[list]:
        """从 OKX 拉取 K 线数据。

        Args:
            symbol: OKX 格式交易对，如 "BTC-USDT"
            timeframe: 时间周期，如 "1h"
            since: 起始毫秒时间戳

        Returns:
            K 线原始数据列表
        """
        ccxt_symbol = self._symbol_to_ccxt(symbol)
        end_ts = kwargs.get("end_ts")

        candles = await self._client.fetch_ohlcv_range(
            ccxt_symbol, timeframe=timeframe, since=since, end=end_ts
        )
        return candles

    def transform(self, raw_data: Any, symbol: str, **kwargs: Any) -> pl.DataFrame:
        """将 CCXT K 线数据转换为标准 DataFrame。

        Args:
            raw_data: [[timestamp, open, high, low, close, volume], ...]
            symbol: 交易对

        Returns:
            标准化 DataFrame
        """
        if not raw_data:
            return pl.DataFrame()

        df = pl.DataFrame(
            {
                "timestamp": [int(c[0]) for c in raw_data],
                "open": [float(c[1]) for c in raw_data],
                "high": [float(c[2]) for c in raw_data],
                "low": [float(c[3]) for c in raw_data],
                "close": [float(c[4]) for c in raw_data],
                "volume": [float(c[5]) for c in raw_data],
                "symbol": [symbol] * len(raw_data),
            }
        )
        return df

    def save(self, df: pl.DataFrame, symbol: str, timeframe: str) -> int:
        """保存 OHLCV 数据到 Parquet。"""
        return self._writer.write_ohlcv(
            df, symbol=symbol, timeframe=timeframe, market_type=self._market_type
        )
