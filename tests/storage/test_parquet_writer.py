"""Parquet 写入器测试。"""

from pathlib import Path

import polars as pl
import pytest

from src.storage.parquet_writer import ParquetWriter


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path / "parquet"


@pytest.fixture
def writer(tmp_dir: Path) -> ParquetWriter:
    return ParquetWriter(tmp_dir)


class TestParquetWriter:
    def test_write_ohlcv_basic(self, writer: ParquetWriter, tmp_dir: Path) -> None:
        """基本写入测试。"""
        df = pl.DataFrame(
            {
                "timestamp": [1704067200000, 1704070800000, 1704074400000],  # 2024-01-01
                "open": [42000.0, 42100.0, 42200.0],
                "high": [42500.0, 42600.0, 42700.0],
                "low": [41800.0, 41900.0, 42000.0],
                "close": [42100.0, 42200.0, 42300.0],
                "volume": [100.0, 200.0, 300.0],
                "symbol": ["BTC-USDT"] * 3,
            }
        )

        written = writer.write_ohlcv(df, "BTC-USDT", "1h")
        assert written == 3

        # 验证文件存在
        parquet_file = tmp_dir / "ohlcv" / "spot" / "BTC-USDT" / "1h" / "2024.parquet"
        assert parquet_file.exists()

    def test_write_ohlcv_dedup(self, writer: ParquetWriter) -> None:
        """写入重复数据应去重。"""
        df = pl.DataFrame(
            {
                "timestamp": [1704067200000, 1704070800000],
                "open": [42000.0, 42100.0],
                "high": [42500.0, 42600.0],
                "low": [41800.0, 41900.0],
                "close": [42100.0, 42200.0],
                "volume": [100.0, 200.0],
                "symbol": ["BTC-USDT"] * 2,
            }
        )

        writer.write_ohlcv(df, "BTC-USDT", "1h")

        # 写入重复 + 新数据
        df2 = pl.DataFrame(
            {
                "timestamp": [1704070800000, 1704078000000],  # 1个重复，1个新
                "open": [42100.0, 42300.0],
                "high": [42600.0, 42800.0],
                "low": [41900.0, 42100.0],
                "close": [42200.0, 42400.0],
                "volume": [200.0, 400.0],
                "symbol": ["BTC-USDT"] * 2,
            }
        )

        written = writer.write_ohlcv(df2, "BTC-USDT", "1h")
        assert written == 1  # 只新增了1条

    def test_write_ohlcv_cross_year(self, writer: ParquetWriter, tmp_dir: Path) -> None:
        """跨年数据应写入不同分区。"""
        df = pl.DataFrame(
            {
                "timestamp": [
                    1703980800000,  # 2023-12-31
                    1704067200000,  # 2024-01-01
                ],
                "open": [42000.0, 42100.0],
                "high": [42500.0, 42600.0],
                "low": [41800.0, 41900.0],
                "close": [42100.0, 42200.0],
                "volume": [100.0, 200.0],
                "symbol": ["BTC-USDT"] * 2,
            }
        )

        writer.write_ohlcv(df, "BTC-USDT", "1h")

        assert (tmp_dir / "ohlcv" / "spot" / "BTC-USDT" / "1h" / "2023.parquet").exists()
        assert (tmp_dir / "ohlcv" / "spot" / "BTC-USDT" / "1h" / "2024.parquet").exists()

    def test_write_empty(self, writer: ParquetWriter) -> None:
        """空数据不应写入。"""
        df = pl.DataFrame(
            {
                "timestamp": [],
                "open": [],
                "high": [],
                "low": [],
                "close": [],
                "volume": [],
                "symbol": [],
            }
        )
        written = writer.write_ohlcv(df, "BTC-USDT", "1h")
        assert written == 0

    def test_read_ohlcv(self, writer: ParquetWriter) -> None:
        """读取写入的数据。"""
        df = pl.DataFrame(
            {
                "timestamp": [1704067200000, 1704070800000, 1704074400000],
                "open": [42000.0, 42100.0, 42200.0],
                "high": [42500.0, 42600.0, 42700.0],
                "low": [41800.0, 41900.0, 42000.0],
                "close": [42100.0, 42200.0, 42300.0],
                "volume": [100.0, 200.0, 300.0],
                "symbol": ["BTC-USDT"] * 3,
            }
        )
        writer.write_ohlcv(df, "BTC-USDT", "1h")

        result = writer.read_ohlcv("BTC-USDT", "1h")
        assert len(result) == 3
        assert result["timestamp"].to_list() == [1704067200000, 1704070800000, 1704074400000]

    def test_read_with_time_filter(self, writer: ParquetWriter) -> None:
        """带时间范围的读取。"""
        df = pl.DataFrame(
            {
                "timestamp": [1704067200000, 1704070800000, 1704074400000],
                "open": [42000.0, 42100.0, 42200.0],
                "high": [42500.0, 42600.0, 42700.0],
                "low": [41800.0, 41900.0, 42000.0],
                "close": [42100.0, 42200.0, 42300.0],
                "volume": [100.0, 200.0, 300.0],
                "symbol": ["BTC-USDT"] * 3,
            }
        )
        writer.write_ohlcv(df, "BTC-USDT", "1h")

        result = writer.read_ohlcv("BTC-USDT", "1h", start_ts=1704070800000)
        assert len(result) == 2
