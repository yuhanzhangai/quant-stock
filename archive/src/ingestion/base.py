"""数据采集基类。"""

from abc import ABC, abstractmethod
from typing import Any

import polars as pl
from loguru import logger

from src.storage.parquet_writer import ParquetWriter
from src.storage.state_tracker import StateTracker


class IngestorBase(ABC):
    """数据采集抽象基类。

    定义采集流程：fetch -> transform -> save -> update_state。
    子类只需实现 fetch 和 transform。
    """

    def __init__(
        self,
        writer: ParquetWriter,
        state_tracker: StateTracker,
        source_name: str,
    ) -> None:
        self._writer = writer
        self._state = state_tracker
        self._source_name = source_name

    @abstractmethod
    async def fetch(self, symbol: str, timeframe: str, since: int | None, **kwargs: Any) -> Any:
        """从交易所拉取原始数据。"""
        ...

    @abstractmethod
    def transform(self, raw_data: Any, symbol: str, **kwargs: Any) -> pl.DataFrame:
        """将原始数据转换为标准 DataFrame。"""
        ...

    async def run(self, symbol: str, timeframe: str, **kwargs: Any) -> int:
        """执行增量采集流程。

        Args:
            symbol: 交易对
            timeframe: 时间周期

        Returns:
            新增数据行数
        """
        # 1. 获取上次采集位置
        last_ts = self._state.get_last_timestamp(self._source_name, symbol, timeframe)
        since = last_ts + 1 if last_ts else None

        if since:
            logger.info(f"增量采集 | {symbol} {timeframe} | 从 {since} 开始")
        else:
            logger.info(f"全量采集 | {symbol} {timeframe}")

        # 2. 拉取数据
        raw = await self.fetch(symbol, timeframe, since=since, **kwargs)

        # 3. 转换
        df = self.transform(raw, symbol)

        if df.is_empty():
            logger.info(f"无新数据 | {symbol} {timeframe}")
            return 0

        # 4. 保存
        written = self.save(df, symbol, timeframe)

        # 5. 更新状态
        if written > 0:
            max_ts = df["timestamp"].max()
            self._state.update_last_timestamp(self._source_name, symbol, timeframe, max_ts)

        return written

    @abstractmethod
    def save(self, df: pl.DataFrame, symbol: str, timeframe: str) -> int:
        """保存数据到存储。"""
        ...
