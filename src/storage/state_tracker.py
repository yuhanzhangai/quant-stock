"""采集状态持久化到 SQLite，支持断点续传。"""

import sqlite3
import time
from pathlib import Path

from loguru import logger


class StateTracker:
    """采集状态追踪器。

    使用 SQLite 持久化每个 (source, symbol, timeframe) 的最新采集时间戳，
    实现增量更新和断点续传。
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._create_tables()
        logger.debug(f"StateTracker 初始化: {db_path}")

    def _create_tables(self) -> None:
        """创建状态表。"""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_state (
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                last_timestamp INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (source, symbol, timeframe)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS universe (
                symbol TEXT PRIMARY KEY,
                market_type TEXT NOT NULL,
                quote_currency TEXT NOT NULL,
                volume_24h REAL,
                rank INTEGER,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.commit()

    def get_last_timestamp(self, source: str, symbol: str, timeframe: str) -> int | None:
        """获取最后采集的时间戳。

        Args:
            source: 数据源，如 "ohlcv", "funding"
            symbol: 交易对
            timeframe: 时间周期

        Returns:
            最后采集的毫秒时间戳，若无记录返回 None
        """
        cursor = self._conn.execute(
            "SELECT last_timestamp FROM ingestion_state "
            "WHERE source=? AND symbol=? AND timeframe=?",
            (source, symbol, timeframe),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def update_last_timestamp(
        self, source: str, symbol: str, timeframe: str, last_timestamp: int
    ) -> None:
        """更新最后采集的时间戳。

        Args:
            source: 数据源
            symbol: 交易对
            timeframe: 时间周期
            last_timestamp: 最新毫秒时间戳
        """
        now = int(time.time() * 1000)
        self._conn.execute(
            """
            INSERT INTO ingestion_state (source, symbol, timeframe, last_timestamp, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (source, symbol, timeframe)
            DO UPDATE SET last_timestamp=excluded.last_timestamp, updated_at=excluded.updated_at
            """,
            (source, symbol, timeframe, last_timestamp, now),
        )
        self._conn.commit()
        logger.debug(f"状态更新 | {source}/{symbol}/{timeframe} -> {last_timestamp}")

    def update_universe(
        self, symbol: str, market_type: str, quote_currency: str, volume_24h: float, rank: int
    ) -> None:
        """更新标的池信息。

        Args:
            symbol: 交易对
            market_type: 市场类型
            quote_currency: 计价币种
            volume_24h: 24小时成交额
            rank: 排名
        """
        now = int(time.time() * 1000)
        self._conn.execute(
            """
            INSERT INTO universe (symbol, market_type, quote_currency, volume_24h, rank, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (symbol)
            DO UPDATE SET volume_24h=excluded.volume_24h,
                rank=excluded.rank, updated_at=excluded.updated_at
            """,
            (symbol, market_type, quote_currency, volume_24h, rank, now),
        )
        self._conn.commit()

    def get_universe(self, market_type: str = "spot", top_n: int = 100) -> list[dict]:
        """获取标的池。

        Args:
            market_type: 市场类型
            top_n: 前 N 名

        Returns:
            标的信息列表
        """
        cursor = self._conn.execute(
            "SELECT symbol, volume_24h, rank FROM universe "
            "WHERE market_type=? ORDER BY rank LIMIT ?",
            (market_type, top_n),
        )
        return [
            {"symbol": row[0], "volume_24h": row[1], "rank": row[2]} for row in cursor.fetchall()
        ]

    def close(self) -> None:
        """关闭连接。"""
        self._conn.close()
        logger.debug("StateTracker 已关闭")
