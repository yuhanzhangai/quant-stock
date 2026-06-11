"""parquet 导出测试:全表落盘/原子性(无 tmp 残留)/meta 最后落且行数一致/失败不抛只告警。

export_meta 契约(dashboard/ledger_reader.py):export_meta.parquet,列 (export_ts, table_name, row_count)。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb
import pytest

from src.execution.ledger import EXPORT_META_NAME, LEDGER_TABLES, LedgerWriter, export_ledger
from src.execution.ledger.ids import signal_id_for


@pytest.fixture
def writer(tmp_path):
    w = LedgerWriter(tmp_path / "ledger.duckdb")
    sid = signal_id_for("1932", "NVDA")
    w.insert_signal(
        signal_id=sid,
        tweet_id="1932",
        handle="@h",
        tier="PROVEN",
        tier_csv_date=date(2026, 6, 10),
        ticker="NVDA",
        direction="bullish",
        call_ts=datetime(2026, 6, 10, 14, 30, tzinfo=UTC),
        tweet_text="t",
        tweet_url="u",
        decision="followed",
        decision_reason="all_gates_passed",
        rule_version="v1.0",
    )
    w.open_order(
        signal_id=sid,
        ticker="NVDA",
        side="buy",
        qty=Decimal("10"),
        order_type="market",
        submitted_ts=datetime(2026, 6, 10, 15, 31, tzinfo=UTC),
        rule_version="v1.0",
    )
    yield w
    w.close()


def read_meta(out) -> dict[str, dict]:
    """meta parquet → {table_name: {export_ts, row_count}}(独立只读连接,Dash 路径)。"""
    ro = duckdb.connect()
    rows = ro.execute(
        f"SELECT export_ts, table_name, row_count FROM read_parquet('{out / EXPORT_META_NAME}')"
    ).fetchall()
    ro.close()
    return {t: {"export_ts": ts, "row_count": n} for ts, t, n in rows}


def test_export_all_tables_with_meta(writer, tmp_path):
    out = tmp_path / "export"
    assert export_ledger(writer.conn, out) is True
    for table in LEDGER_TABLES:
        assert (out / f"{table}.parquet").exists(), table
    # 原子性:无临时文件残留
    assert list(out.glob(".*tmp")) == []
    meta = read_meta(out)
    # 契约列序与内容:每表一行,同一 export_ts
    assert set(meta) == set(LEDGER_TABLES)
    assert meta["signals"]["row_count"] == 1
    assert meta["orders"]["row_count"] == 1
    assert len({v["export_ts"] for v in meta.values()}) == 1
    # 快照可被独立进程式只读(Dash 路径):新连接直接读 parquet
    ro = duckdb.connect()
    n = ro.execute(f"SELECT count(*) FROM read_parquet('{out / 'orders.parquet'}')").fetchone()[0]
    assert n == 1
    ro.close()


def test_export_meta_written_last_reflects_snapshot(writer, tmp_path):
    out = tmp_path / "export"
    export_ledger(writer.conn, out)
    first = read_meta(out)
    writer.append_order_event(
        order_id=writer.conn.execute("SELECT order_id FROM orders LIMIT 1").fetchone()[0],
        status="filled",
    )
    export_ledger(writer.conn, out)
    second = read_meta(out)
    # 行数只增不减(append-only,审计可对照)
    assert second["orders"]["row_count"] == first["orders"]["row_count"] + 1
    assert second["orders"]["export_ts"] >= first["orders"]["export_ts"]


def test_export_failure_returns_false_never_raises(writer, tmp_path):
    blocked = tmp_path / "not_a_dir"
    blocked.write_text("占位文件,mkdir 必失败")
    assert export_ledger(writer.conn, blocked) is False  # 只告警,不向上抛(记账优先)


def test_export_meta_readable_by_dash_reader_contract(writer, tmp_path):
    """契约端到端:用 main 上 Dash reader 的判稳逻辑等价检查(meta 存在 + 列可读)。"""
    out = tmp_path / "export"
    export_ledger(writer.conn, out)
    # Dash export_available() 等价:meta 文件存在即快照集完整
    assert (out / EXPORT_META_NAME).exists()
    # Dash 取最新 export_ts 判 STALE 的查询可跑
    ro = duckdb.connect()
    ts = ro.execute(f"SELECT max(export_ts) FROM read_parquet('{out / EXPORT_META_NAME}')").fetchone()[0]
    ro.close()
    assert ts is not None


def test_no_json_meta_left(writer, tmp_path):
    """防回归:旧 JSON meta 不再产出(契约是 parquet)。"""
    out = tmp_path / "export"
    export_ledger(writer.conn, out)
    assert not (out / "export_meta.json").exists()
