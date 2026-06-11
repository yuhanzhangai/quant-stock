"""模拟盘 ledger 取数薄层 — 真实现:读 Exec writer 的 parquet 导出(spec r3 §2)。

Dash 永不直连 ledger.duckdb(跨进程锁实测互斥,见 DASH_SIGNOFF §1):
唯一数据面 = writer 每循环收尾导出的全表 parquet + 最后原子落的 export_meta。
视图(v_orders_current 等)不在导出里,本模块用 spec r3 §4.7 的 SQL
在 DuckDB **内存库**上对 parquet 现算——口径与 ledger 库内视图逐字一致。

导出契约(待 P1 实施时与 Exec 最终对齐,默认值如下):
- 目录:data/execution/export/(可用环境变量 LEDGER_EXPORT_DIR 覆盖)
- 文件:<table>.parquet(全量,append-only 行数只增不减)
- export_meta.parquet:列 (export_ts, table_name, row_count),全部表成功后最后原子落
  → meta 存在即快照集完整;meta 陈旧 = Exec 离线/HALT,页面降级提示而非报错(§2)。
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

IS_MOCK = False

EXPORT_DIR = Path(
    os.environ.get("LEDGER_EXPORT_DIR", Path(__file__).resolve().parents[1] / "data" / "execution" / "export")
)
META_PATH = EXPORT_DIR / "export_meta.parquet"

# 执行循环 ~15min/轮(人类节奏);3 轮没有新导出按陈旧降级
STALE_AFTER = timedelta(minutes=45)

TABLES = (
    "signals",
    "orders",
    "fills",
    "positions_daily",
    "pdt_ledger",
    "account_daily",
    "agent_runs",
    "ingest_watermark",
)

# spec r3 §4.7 视图 SQL,逐字照抄(对账口径不自造)
_VIEW_SQL = """
CREATE OR REPLACE VIEW v_orders_current AS
SELECT * FROM orders
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY seq DESC) = 1;

CREATE OR REPLACE VIEW v_fills_effective AS
SELECT * FROM fills f
WHERE f.voids_fill_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM fills v WHERE v.voids_fill_id = f.fill_id);

CREATE OR REPLACE VIEW v_order_filled AS
SELECT order_id,
       sum(qty)                               AS filled_qty,
       sum(qty * price) / nullif(sum(qty), 0) AS avg_fill_price,
       min(fill_ts)                           AS first_fill_ts,
       max(fill_ts)                           AS last_fill_ts,
       count(*)                               AS n_fills
FROM v_fills_effective
GROUP BY order_id;

CREATE OR REPLACE VIEW v_positions_eod AS
SELECT * FROM positions_daily
QUALIFY row_number() OVER (PARTITION BY snapshot_date, ticker ORDER BY snapshot_ts DESC) = 1;

CREATE OR REPLACE VIEW v_pdt_latest AS
SELECT * FROM pdt_ledger
QUALIFY row_number() OVER (ORDER BY event_ts DESC, entry_id DESC) = 1;
"""


def export_available() -> bool:
    """meta 在 = 快照集完整(meta 最后原子落,§2)。"""
    return META_PATH.exists()


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()  # 内存库,无文件锁
    for t in TABLES:
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{EXPORT_DIR / f'{t}.parquet'}')")
    con.execute(_VIEW_SQL)
    return con


def _q(sql: str, params: list | None = None) -> pd.DataFrame:
    con = _con()
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def load_export_meta() -> pd.DataFrame:
    return pd.read_parquet(META_PATH)


def export_ts() -> datetime:
    ts = pd.to_datetime(load_export_meta()["export_ts"].max())
    return ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)


def freshness() -> tuple[str, float | None, datetime | None]:
    """('missing'|'stale'|'fresh', 距导出分钟数, 导出时间)——§2 降级判据。"""
    if not export_available():
        return "missing", None, None
    ts = export_ts()
    age = datetime.now(UTC) - ts
    return ("stale" if age > STALE_AFTER else "fresh"), age.total_seconds() / 60, ts


# ── 与 ledger_mock 同名同列的 load_* API(页面只认这层)──────────────


def load_signals() -> pd.DataFrame:
    return _q("SELECT * FROM signals ORDER BY call_ts DESC")


def load_orders_current() -> pd.DataFrame:
    return _q("SELECT * FROM v_orders_current ORDER BY submitted_ts DESC")


def load_order_events(order_id: str) -> pd.DataFrame:
    return _q("SELECT * FROM orders WHERE order_id = ? ORDER BY seq", [order_id])


def load_fills_effective() -> pd.DataFrame:
    return _q("SELECT * FROM v_fills_effective ORDER BY fill_ts DESC")


def load_order_filled() -> pd.DataFrame:
    return _q("SELECT * FROM v_order_filled")


def load_positions_eod(days: int = 10) -> pd.DataFrame:
    return _q(
        "SELECT * FROM v_positions_eod "
        "WHERE snapshot_date >= (SELECT max(snapshot_date) FROM v_positions_eod) - ? "
        "ORDER BY snapshot_date, ticker",
        [days],
    )


def load_pdt_latest() -> pd.DataFrame:
    return _q("SELECT * FROM v_pdt_latest")


def load_ingest_watermark_latest() -> pd.DataFrame:
    return _q("SELECT * FROM ingest_watermark ORDER BY poll_ts DESC LIMIT 1")


def load_recon_status(days: int = 7) -> pd.DataFrame:
    """§7 对账结果。结构化 recon 字段待 Valid 的 recon_runs/findings 落地后对齐(r3 裁决);
    过渡期按 spec §7 现行口径解析 eod_snapshot 的 note(recon=ok)。"""
    df = _q(
        "SELECT trade_date, coalesce(note, '') AS note FROM pdt_ledger "
        "WHERE event_type = 'eod_snapshot' "
        "QUALIFY row_number() OVER (PARTITION BY trade_date ORDER BY event_ts DESC) = 1 "
        "ORDER BY trade_date DESC LIMIT ?",
        [days],
    )
    df["recon"] = df["note"].map(lambda s: "ok" if "recon=ok" in s else "mismatch")
    return df[["trade_date", "recon"]].sort_values("trade_date")


def load_account_daily(days: int = 10) -> pd.DataFrame:
    """账户级每日快照(r3 §4.5b account_daily;同日重抓取最新 snapshot_ts)。"""
    return _q(
        "SELECT * FROM account_daily "
        "QUALIFY row_number() OVER (PARTITION BY snapshot_date ORDER BY snapshot_ts DESC) = 1 "
        "ORDER BY snapshot_date DESC LIMIT ?",
        [days],
    ).sort_values("snapshot_date")


def load_agent_runs(n: int = 20) -> pd.DataFrame:
    """执行循环心跳(r3 §4.5b agent_runs;finished_ts 为 NULL = 该轮崩溃未收尾,本身即证据)。"""
    return _q("SELECT * FROM agent_runs ORDER BY started_ts DESC LIMIT ?", [n])
