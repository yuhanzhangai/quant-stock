"""DuckDB 客户端测试。"""

from pathlib import Path

import polars as pl
import pytest

from src.storage.duckdb_client import DuckDBClient
from src.storage.parquet_writer import ParquetWriter


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.duckdb"


@pytest.fixture
def parquet_dir(tmp_path: Path) -> Path:
    return tmp_path / "parquet"


class TestDuckDBClient:
    def test_basic_query(self, db_path: Path) -> None:
        """基本查询测试。"""
        with DuckDBClient(db_path) as db:
            result = db.query_scalar("SELECT 1 + 1")
            assert result == 2

    def test_query_df(self, db_path: Path) -> None:
        """DataFrame 查询测试。"""
        with DuckDBClient(db_path) as db:
            df = db.query_df("SELECT 42 as val, 'hello' as msg")
            assert len(df) == 1
            assert df["val"][0] == 42

    def test_read_parquet_view(
        self, db_path: Path, parquet_dir: Path
    ) -> None:
        """测试通过视图读取 Parquet 数据。"""
        # 先写一些 Parquet 数据
        writer = ParquetWriter(parquet_dir)
        df = pl.DataFrame({
            "timestamp": [1704067200000, 1704070800000],
            "open": [42000.0, 42100.0],
            "high": [42500.0, 42600.0],
            "low": [41800.0, 41900.0],
            "close": [42100.0, 42200.0],
            "volume": [100.0, 200.0],
            "symbol": ["BTC-USDT"] * 2,
        })
        writer.write_ohlcv(df, "BTC-USDT", "1h")

        # 用 DuckDB 直接查
        with DuckDBClient(db_path, parquet_dir) as db:
            result = db.query_df("SELECT COUNT(*) as cnt FROM ohlcv")
            assert result["cnt"][0] == 2

    def test_execute(self, db_path: Path) -> None:
        """执行 DDL 测试。"""
        with DuckDBClient(db_path) as db:
            db.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")
            db.execute("INSERT INTO test VALUES (1, 'hello')")
            result = db.query_scalar("SELECT COUNT(*) FROM test")
            assert result == 1
