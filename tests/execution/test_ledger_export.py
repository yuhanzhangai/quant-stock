"""parquet 导出测试:全表落盘/原子性(无 tmp 残留)/meta 最后落且行数一致/失败不抛只告警。"""

import json
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


def test_export_all_tables_with_meta(writer, tmp_path):
    out = tmp_path / "export"
    assert export_ledger(writer.conn, out) is True
    for table in LEDGER_TABLES:
        assert (out / f"{table}.parquet").exists(), table
    # 原子性:无临时文件残留
    assert list(out.glob(".*tmp")) == []
    meta = json.loads((out / EXPORT_META_NAME).read_text(encoding="utf-8"))
    assert meta["row_counts"]["signals"] == 1
    assert meta["row_counts"]["orders"] == 1
    assert meta["exported_at"]
    # 快照可被独立进程式只读(Dash 路径):新连接直接读 parquet
    ro = duckdb.connect()
    n = ro.execute(f"SELECT count(*) FROM read_parquet('{out / 'orders.parquet'}')").fetchone()[0]
    assert n == 1
    ro.close()


def test_export_meta_written_last_reflects_snapshot(writer, tmp_path):
    out = tmp_path / "export"
    export_ledger(writer.conn, out)
    first = json.loads((out / EXPORT_META_NAME).read_text(encoding="utf-8"))
    writer.append_order_event(
        order_id=writer.conn.execute("SELECT order_id FROM orders LIMIT 1").fetchone()[0],
        status="filled",
    )
    export_ledger(writer.conn, out)
    second = json.loads((out / EXPORT_META_NAME).read_text(encoding="utf-8"))
    # 行数只增不减(append-only,审计可对照)
    assert second["row_counts"]["orders"] == first["row_counts"]["orders"] + 1
    assert second["exported_at"] >= first["exported_at"]


def test_export_failure_returns_false_never_raises(writer, tmp_path):
    blocked = tmp_path / "not_a_dir"
    blocked.write_text("占位文件,mkdir 必失败")
    assert export_ledger(writer.conn, blocked) is False  # 只告警,不向上抛(记账优先)
