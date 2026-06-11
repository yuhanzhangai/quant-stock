"""每循环 parquet 全量导出 + export_meta 新鲜度标记(spec §2,读写分离)。

原子性约定(Exec 会签三要点,r3 已吸收):
1. 每个 parquet 先写临时文件再 os.replace 原子换名——Dash 永远读不到半成品;
2. export_meta.json 在**全部表成功后最后**原子落——meta 新=快照集完整一致,
   meta 旧=exec 离线/HALT,Dash 降级显示"数据陈旧"而非报错;
3. 导出失败不阻断落账(记账优先):本模块只告警返回 False,绝不向上抛。
   kill-switch 不阻断导出(kill 只停页面动作),HALT 后末轮快照仍落。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from loguru import logger

EXPORT_META_NAME = "export_meta.json"

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

        meta = {
            "exported_at": datetime.now(UTC).isoformat(),
            "schema": "ORDER_LEDGER_SPEC r3",
            "tables": list(LEDGER_TABLES),
            "row_counts": row_counts,
        }
        tmp_meta = export_dir / f".{EXPORT_META_NAME}.tmp"
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_meta, export_dir / EXPORT_META_NAME)
        logger.debug("ledger 导出完成: {} 表 → {}", len(LEDGER_TABLES), export_dir)
        return True
    except Exception as exc:  # noqa: BLE001 — 导出失败不阻断落账(spec §2),只告警
        logger.warning("ledger 导出失败(不阻断落账): {}", exc)
        return False
