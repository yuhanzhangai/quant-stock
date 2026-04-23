"""因子基类。"""

from abc import ABC, abstractmethod
from pathlib import Path

import polars as pl
from loguru import logger


class FactorBase(ABC):
    """因子抽象基类。

    所有因子继承此类，实现 compute 方法。
    支持因子值缓存到 Parquet。
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir

    @property
    @abstractmethod
    def name(self) -> str:
        """因子名称。"""
        ...

    @property
    @abstractmethod
    def dependencies(self) -> list[str]:
        """需要的列名列表。"""
        ...

    @abstractmethod
    def compute(self, df: pl.DataFrame) -> pl.Series:
        """计算因子值。

        Args:
            df: 输入数据，包含 dependencies 指定的列

        Returns:
            因子值 Series
        """
        ...

    def compute_cached(self, df: pl.DataFrame, symbol: str, timeframe: str) -> pl.Series:
        """带缓存的因子计算。

        Args:
            df: 输入数据
            symbol: 交易对
            timeframe: 时间周期

        Returns:
            因子值 Series
        """
        if self._cache_dir:
            cache_path = self._cache_dir / self.name / f"{symbol}_{timeframe}.parquet"
            if cache_path.exists():
                cached = pl.read_parquet(cache_path)
                max_cached_ts = cached["timestamp"].max()
                max_input_ts = df["timestamp"].max()
                if max_cached_ts >= max_input_ts:
                    logger.debug(f"因子缓存命中 | {self.name} | {symbol} {timeframe}")
                    # 按输入的 timestamp 对齐返回
                    merged = df.select("timestamp").join(cached, on="timestamp", how="left")
                    return merged[self.name]

        # 计算
        result = self.compute(df)

        # 缓存
        if self._cache_dir:
            cache_path = self._cache_dir / self.name / f"{symbol}_{timeframe}.parquet"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_df = df.select("timestamp").with_columns(result.alias(self.name))
            cache_df.write_parquet(cache_path)
            logger.debug(f"因子缓存写入 | {self.name} | {symbol} {timeframe}")

        return result
