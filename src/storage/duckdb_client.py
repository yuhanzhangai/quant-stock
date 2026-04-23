"""DuckDB 客户端封装，自动创建视图读取 Parquet 文件。"""

from pathlib import Path
from typing import Any, Optional

import duckdb
import polars as pl
from loguru import logger


class DuckDBClient:
    """DuckDB 客户端。

    提供查询、执行、视图创建功能。
    启动时自动注册 Parquet 文件为可查询视图。
    """

    def __init__(self, db_path: Path, parquet_dir: Optional[Path] = None) -> None:
        self._db_path = db_path
        self._parquet_dir = parquet_dir
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(db_path))
        logger.info(f"DuckDB 连接已建立: {db_path}")

        if parquet_dir:
            self._register_parquet_views()

    def _register_parquet_views(self) -> None:
        """扫描 Parquet 目录，为每种数据类型创建视图。"""
        if not self._parquet_dir or not self._parquet_dir.exists():
            logger.debug("Parquet 目录不存在，跳过视图注册")
            return

        # OHLCV 视图
        ohlcv_pattern = str(self._parquet_dir / "ohlcv" / "*" / "*" / "*" / "*.parquet")
        try:
            self._conn.execute(
                f"CREATE OR REPLACE VIEW ohlcv AS SELECT * FROM read_parquet('{ohlcv_pattern}', union_by_name=true, filename=true)"
            )
            logger.debug("注册视图: ohlcv")
        except duckdb.IOException:
            logger.debug("OHLCV Parquet 文件尚未创建，跳过视图注册")

        # Funding 视图
        funding_pattern = str(self._parquet_dir / "funding" / "*.parquet")
        try:
            self._conn.execute(
                f"CREATE OR REPLACE VIEW funding AS SELECT * FROM read_parquet('{funding_pattern}', union_by_name=true, filename=true)"
            )
            logger.debug("注册视图: funding")
        except duckdb.IOException:
            logger.debug("Funding Parquet 文件尚未创建，跳过视图注册")

    def execute(self, sql: str, params: Optional[list[Any]] = None) -> None:
        """执行 SQL 语句。

        Args:
            sql: SQL 语句
            params: 参数列表
        """
        if params:
            self._conn.execute(sql, params)
        else:
            self._conn.execute(sql)

    def query_df(self, sql: str, params: Optional[list[Any]] = None) -> pl.DataFrame:
        """执行查询并返回 Polars DataFrame。

        Args:
            sql: SQL 查询
            params: 参数列表

        Returns:
            查询结果 DataFrame
        """
        if params:
            result = self._conn.execute(sql, params)
        else:
            result = self._conn.execute(sql)
        return result.pl()

    def query_scalar(self, sql: str, params: Optional[list[Any]] = None) -> Any:
        """执行查询并返回单个标量值。

        Args:
            sql: SQL 查询
            params: 参数列表

        Returns:
            单个值
        """
        if params:
            result = self._conn.execute(sql, params).fetchone()
        else:
            result = self._conn.execute(sql).fetchone()
        return result[0] if result else None

    def close(self) -> None:
        """关闭连接。"""
        self._conn.close()
        logger.debug("DuckDB 连接已关闭")

    def __enter__(self) -> "DuckDBClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
