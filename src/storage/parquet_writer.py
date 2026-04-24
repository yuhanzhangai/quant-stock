"""Parquet 文件写入器，按 symbol/timeframe/year 分区，支持增量追加+去重。"""

from pathlib import Path

import polars as pl
from loguru import logger


class ParquetWriter:
    """Parquet 分区写入器。

    数据按 symbol/timeframe/year 分区存储，支持增量追加和按时间戳去重。
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _get_partition_path(self, data_type: str, market_type: str, symbol: str, timeframe: str, year: int) -> Path:
        """构建分区文件路径。

        Args:
            data_type: 数据类型，如 "ohlcv", "funding"
            market_type: 市场类型，如 "spot", "swap"
            symbol: 交易对，如 "BTC-USDT"
            timeframe: 时间周期，如 "1h"
            year: 年份

        Returns:
            Parquet 文件路径
        """
        return self._base_dir / data_type / market_type / symbol / timeframe / f"{year}.parquet"

    def write_ohlcv(
        self,
        df: pl.DataFrame,
        symbol: str,
        timeframe: str,
        market_type: str = "spot",
    ) -> int:
        """写入 OHLCV 数据到 Parquet，按年分区，自动去重。

        Args:
            df: 包含 timestamp, open, high, low, close, volume 列的 DataFrame
            symbol: 交易对
            timeframe: 时间周期
            market_type: 市场类型

        Returns:
            实际写入的行数
        """
        if df.is_empty():
            logger.debug(f"空数据，跳过写入 | {symbol} {timeframe}")
            return 0

        total_written = 0

        # 按年分组写入
        df_with_year = df.with_columns(pl.from_epoch("timestamp", time_unit="ms").dt.year().alias("_year"))

        for year in df_with_year["_year"].unique().sort().to_list():
            year_df = df_with_year.filter(pl.col("_year") == year).drop("_year")
            path = self._get_partition_path("ohlcv", market_type, symbol, timeframe, year)
            written = self._append_and_dedup(path, year_df, dedup_col="timestamp")
            total_written += written

        logger.info(f"OHLCV 写入完成 | {symbol} {timeframe} | 输入: {len(df)} 行 | 实际写入: {total_written} 行")
        return total_written

    def write_funding(self, df: pl.DataFrame, symbol: str) -> int:
        """写入资金费率数据到 Parquet。

        Args:
            df: 包含 funding_time, funding_rate 等列的 DataFrame
            symbol: 合约 ID

        Returns:
            实际写入的行数
        """
        if df.is_empty():
            return 0

        path = self._base_dir / "funding" / f"{symbol}.parquet"
        written = self._append_and_dedup(path, df, dedup_col="funding_time")

        logger.info(f"Funding 写入完成 | {symbol} | 写入: {written} 行")
        return written

    def _append_and_dedup(self, path: Path, new_df: pl.DataFrame, dedup_col: str) -> int:
        """追加数据并去重。

        Args:
            path: Parquet 文件路径
            new_df: 新数据
            dedup_col: 去重列名

        Returns:
            最终新增的行数
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            existing = pl.read_parquet(path)
            combined = pl.concat([existing, new_df])
            combined = combined.unique(subset=[dedup_col]).sort(dedup_col)
            new_rows = len(combined) - len(existing)
        else:
            combined = new_df.unique(subset=[dedup_col]).sort(dedup_col)
            new_rows = len(combined)

        combined.write_parquet(path)
        logger.debug(f"写入 {path} | 新增: {new_rows} 行 | 总计: {len(combined)} 行")
        return new_rows

    def read_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        market_type: str = "spot",
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pl.DataFrame:
        """读取 OHLCV 数据。

        Args:
            symbol: 交易对
            timeframe: 时间周期
            market_type: 市场类型
            start_ts: 起始毫秒时间戳
            end_ts: 结束毫秒时间戳

        Returns:
            OHLCV DataFrame
        """
        pattern = self._base_dir / "ohlcv" / market_type / symbol / timeframe / "*.parquet"
        files = sorted(pattern.parent.glob("*.parquet"))

        if not files:
            logger.warning(f"未找到数据文件 | {symbol} {timeframe}")
            return pl.DataFrame()

        dfs = [pl.read_parquet(f) for f in files]
        df = pl.concat(dfs).sort("timestamp")

        if start_ts is not None:
            df = df.filter(pl.col("timestamp") >= start_ts)
        if end_ts is not None:
            df = df.filter(pl.col("timestamp") <= end_ts)

        return df
