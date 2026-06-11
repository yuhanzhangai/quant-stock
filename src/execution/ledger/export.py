"""每循环 parquet 全量导出 + export_meta 新鲜度标记(spec §2,读写分离)。

原子性约定(Exec 会签三要点,r3 已吸收):
1. 每个 parquet 先写临时文件再 os.replace 原子换名——Dash 永远读不到半成品;
2. export_meta 在**全部表成功后最后**原子落——meta 新=快照集完整一致,
   meta 旧=exec 离线/HALT,Dash 降级显示"数据陈旧"而非报错;
3. 导出失败不阻断落账(记账优先):本模块只告警返回 False,绝不向上抛。
   kill-switch 不阻断导出(kill 只停页面动作),HALT 后末轮快照仍落。

导出契约(与 dashboard/ledger_reader.py 对齐,改动必须双方同步,不得静默偏离):
- 目录默认 data/execution/export/(Dash 侧可用 LEDGER_EXPORT_DIR 覆盖);
- 每表一个 <table>.parquet,全量,行数只增不减;
- export_meta.parquet 列 (export_ts, table_name, row_count),一表一行。
  注:本侧多导 ledger_meta 表(审计对照用),Dash 契约表清单不含它,属契约外冗余。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from loguru import logger

EXPORT_META_NAME = "export_meta.parquet"

# 全量导出表清单(paper 量级,不做增量);行数只增不减,审计可对照(§2)
LEDGER_TABLES: tuple[str, ...] = (
    "signals",
    "orders",
    "fills",
    "positions_daily",
    "pdt_ledger",
    "account_daily",
    "agent_runs",
    "ingest_watermark",
    "ledger_meta",
)


def export_ledger(conn: duckdb.DuckDBPyConnection, export_dir: Path) -> bool:
    """把 ledger 全表导出为 parquet 快照。成功返回 True;任何失败告警返回 False。"""
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        row_counts: dict[str, int] = {}
        for table in LEDGER_TABLES:
            tmp = export_dir / f".{table}.parquet.tmp"
            conn.execute(f"COPY (SELECT * FROM {table}) TO '{tmp}' (FORMAT PARQUET)")
            os.replace(tmp, export_dir / f"{table}.parquet")
            row_counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        # TEMP 表只活在本连接会话,不落 ledger 库文件,不破坏 append-only
        export_ts = datetime.now(UTC)
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE _export_meta (export_ts TIMESTAMPTZ, table_name TEXT, row_count BIGINT)"
        )
        for table, count in row_counts.items():
            conn.execute("INSERT INTO _export_meta VALUES (?, ?, ?)", [export_ts, table, count])
        tmp_meta = export_dir / f".{EXPORT_META_NAME}.tmp"
        conn.execute(f"COPY _export_meta TO '{tmp_meta}' (FORMAT PARQUET)")
        conn.execute("DROP TABLE _export_meta")
        os.replace(tmp_meta, export_dir / EXPORT_META_NAME)
        logger.debug("ledger 导出完成: {} 表 → {}", len(LEDGER_TABLES), export_dir)
        return True
    except Exception as exc:  # noqa: BLE001 — 导出失败不阻断落账(spec §2),只告警
        logger.warning("ledger 导出失败(不阻断落账): {}", exc)
        return False
