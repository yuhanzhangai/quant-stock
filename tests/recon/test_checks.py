"""A 组对账检查器测试:每项不变量一个干净例 + 违反例(RECON_DESIGN_V0 §6:内存 DuckDB 构造违反样例验证判级)。

fixture DDL = ORDER_LEDGER_SPEC §4 检查器消费的列子集 + §4.7 视图**原样**——视图语义(QUALIFY 最新行、
作废对剔除)是多项检查的前提,必须与 spec 一字不差。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from src.recon.checks import run_checks, run_recon

# 2026-06-08 = 周一(NYSE 开市);全部用例围绕这一周
_CALL = datetime(2026, 6, 8, 14, 0, tzinfo=UTC)
_INGESTED = _CALL + timedelta(minutes=30)
_SUBMITTED = _CALL + timedelta(hours=1)
_FILL = _SUBMITTED + timedelta(minutes=5)
_NOW = datetime(2026, 6, 8, 18, 0, tzinfo=UTC)

_DDL = """
CREATE TABLE signals (signal_id TEXT PRIMARY KEY, call_ts TIMESTAMPTZ, ingested_ts TIMESTAMPTZ, decision TEXT);
CREATE TABLE orders (
    order_id TEXT, seq INTEGER, signal_id TEXT, ticker TEXT, side TEXT, qty DECIMAL(18,4),
    submitted_ts TIMESTAMPTZ, status TEXT, corrects_seq INTEGER, exit_reason TEXT,
    exit_trigger_signal_id TEXT, PRIMARY KEY (order_id, seq));
CREATE TABLE fills (fill_id TEXT PRIMARY KEY, order_id TEXT, fill_ts TIMESTAMPTZ,
    qty DECIMAL(18,4), price DECIMAL(18,4), voids_fill_id TEXT);
CREATE TABLE ingest_watermark (poll_ts TIMESTAMPTZ, last_seen_call_ts TIMESTAMPTZ, calls_seen INTEGER);
-- 视图与 ORDER_LEDGER_SPEC §4.7 原样一致
CREATE VIEW v_orders_current AS SELECT * FROM orders
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY seq DESC) = 1;
CREATE VIEW v_fills_effective AS SELECT * FROM fills f
WHERE f.voids_fill_id IS NULL AND NOT EXISTS (SELECT 1 FROM fills v WHERE v.voids_fill_id = f.fill_id);
CREATE VIEW v_order_filled AS SELECT order_id, sum(qty) AS filled_qty,
    sum(qty * price) / nullif(sum(qty), 0) AS avg_fill_price, min(fill_ts) AS first_fill_ts,
    max(fill_ts) AS last_fill_ts, count(*) AS n_fills FROM v_fills_effective GROUP BY order_id;
"""


def _order(con, oid, seq, status, *, sid="sig_t1_AAA", ticker="AAA", side="buy", qty=10,
           submitted=_SUBMITTED, corrects=None, exit_reason=None, trigger=None):
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [oid, seq, sid, ticker, side, qty, submitted, status, corrects, exit_reason, trigger])


def _fill(con, fid, oid, *, ts=_FILL, qty=10, price=100.0, voids=None):
    con.execute("INSERT INTO fills VALUES (?,?,?,?,?,?)", [fid, oid, ts, qty, price, voids])


@pytest.fixture()
def ledger() -> duckdb.DuckDBPyConnection:
    """干净 ledger:一笔 followed 跟单全成 + 21d 平仓全成 + 新鲜水位。所有不变量成立。"""
    con = duckdb.connect()
    con.execute(_DDL)
    con.execute("INSERT INTO signals VALUES ('sig_t1_AAA', ?, ?, 'followed')", [_CALL, _INGESTED])
    con.execute("INSERT INTO signals VALUES ('sig_t2_BBB', ?, ?, 'skipped')", [_CALL, _INGESTED])
    _order(con, "ord_1", 0, "submitted")
    _order(con, "ord_1", 1, "filled")
    _fill(con, "fil_1", "ord_1")
    exit_sub = _SUBMITTED + timedelta(days=30)
    _order(con, "ord_2", 0, "submitted", side="sell", submitted=exit_sub, exit_reason="hold_21d")
    _order(con, "ord_2", 1, "filled", side="sell", submitted=exit_sub, exit_reason="hold_21d")
    _fill(con, "fil_2", "ord_2", ts=exit_sub + timedelta(minutes=5), price=110.0)
    con.execute("INSERT INTO ingest_watermark VALUES (?, ?, 3)", [_NOW - timedelta(minutes=30), _CALL])
    return con


def _now_after_exit() -> datetime:
    return _SUBMITTED + timedelta(days=30, hours=3)


def _ids(result) -> set[str]:
    return {f.check_id for f in result.findings}


def test_clean_ledger_passes(ledger):
    con = ledger
    con.execute("UPDATE ingest_watermark SET poll_ts = ?", [_now_after_exit() - timedelta(minutes=30)])
    result = run_checks(con, now=_now_after_exit())
    assert result.result == "pass" and not result.findings


def test_a1_orphan_fill_halts(ledger):
    _fill(ledger, "fil_ghost", "ord_ghost")
    result = run_checks(ledger, now=_now_after_exit())
    assert result.result == "halt"
    assert any(f.check_id == "A1" and f.order_id == "ord_ghost" for f in result.findings)


def test_a2_overfill_halts(ledger):
    _fill(ledger, "fil_extra", "ord_1", qty=5)  # 10 委托已全成,再来 5 = 重复回采
    assert "A2" in _ids(run_checks(ledger, now=_now_after_exit()))


def test_a2_voided_fill_not_counted(ledger):
    _fill(ledger, "fil_bad", "ord_1", qty=5)
    _fill(ledger, "fil_void", "ord_1", qty=5, voids="fil_bad")  # 作废对剔除后不超额
    assert "A2" not in _ids(run_checks(ledger, now=_now_after_exit()))


def test_a3_status_fill_mismatch(ledger):
    _order(ledger, "ord_rej", 0, "submitted", sid="sig_t1_AAA")
    _order(ledger, "ord_rej", 1, "rejected", sid="sig_t1_AAA")
    _fill(ledger, "fil_rej", "ord_rej", qty=10)  # rejected 却有成交
    found = [f for f in run_checks(ledger, now=_now_after_exit()).findings if f.check_id == "A3"]
    assert any(f.order_id == "ord_rej" for f in found)


def test_a3_cancelled_with_fill_needs_partial_history(ledger):
    # 无 partial 历史的 cancelled + fill → halt
    _order(ledger, "ord_c1", 0, "submitted")
    _order(ledger, "ord_c1", 1, "cancelled")
    _fill(ledger, "fil_c1", "ord_c1", qty=4)
    # 有 partial 历史的 cancelled + fill → 合法(余量撤单)
    _order(ledger, "ord_c2", 0, "submitted")
    _order(ledger, "ord_c2", 1, "partial")
    _order(ledger, "ord_c2", 2, "cancelled")
    _fill(ledger, "fil_c2", "ord_c2", qty=4)
    found = [f for f in run_checks(ledger, now=_now_after_exit()).findings if f.check_id == "A3"]
    assert any(f.order_id == "ord_c1" for f in found)
    assert not any(f.order_id == "ord_c2" for f in found)


def test_a4_stale_submitted_warn_then_halt(ledger):
    _order(ledger, "ord_stuck", 0, "submitted")
    _fill(ledger, "fil_stuck", "ord_stuck", ts=_FILL, qty=10)
    # 同日回采周期内:不触发(2026-06-08 周一)
    same_day = [f for f in run_checks(ledger, now=_NOW).findings if f.check_id == "A4"]
    assert not same_day
    # 2 个交易日后(周三)→ warn;4 个交易日后(周五)→ halt
    for now, expected in ((datetime(2026, 6, 10, 18, 0, tzinfo=UTC), "warn"),
                          (datetime(2026, 6, 12, 18, 0, tzinfo=UTC), "halt")):
        found = [f for f in run_checks(ledger, now=now).findings
                 if f.check_id == "A4" and f.order_id == "ord_stuck"]
        assert found and found[0].severity == expected, (now, expected)


def test_a5_seq_gap_and_post_terminal(ledger):
    _order(ledger, "ord_gap", 0, "submitted")
    _order(ledger, "ord_gap", 2, "filled")  # seq 缺 1
    _fill(ledger, "fil_gap", "ord_gap")
    _order(ledger, "ord_post", 0, "filled")
    _fill(ledger, "fil_post", "ord_post")
    _order(ledger, "ord_post", 1, "cancelled")  # 终态后非更正行
    found = [f for f in run_checks(ledger, now=_now_after_exit()).findings if f.check_id == "A5"]
    assert {f.order_id for f in found} == {"ord_gap", "ord_post"}


def test_a5_correction_row_exempt(ledger):
    _order(ledger, "ord_fix", 0, "filled")  # 误记终态
    _fill(ledger, "fil_fix", "ord_fix")
    _order(ledger, "ord_fix", 1, "partial", corrects=0)  # r3 更正行:终态封锁唯一豁免
    found = [f for f in run_checks(ledger, now=_now_after_exit()).findings
             if f.check_id == "A5" and f.order_id == "ord_fix"]
    assert not found


def test_a5_correction_resets_terminal_lock(ledger):
    """P1 演练剧本 B:误记终态 → 更正回非终态 → 正常迁移到真终态,全程合法(v0 误报回归钉)。"""
    _order(ledger, "ord_b", 0, "submitted")
    _order(ledger, "ord_b", 1, "filled")  # 误记终态
    _order(ledger, "ord_b", 2, "submitted", corrects=1)  # 更正回非终态:锁重置
    _order(ledger, "ord_b", 3, "expired")  # 正常迁移,不应报
    assert not [f for f in run_checks(ledger, now=_now_after_exit()).findings
                if f.check_id == "A5" and f.order_id == "ord_b"]
    # 反例:更正为终态值后,再来非更正行仍须报
    _order(ledger, "ord_b2", 0, "filled")
    _fill(ledger, "fil_b2", "ord_b2")
    _order(ledger, "ord_b2", 1, "expired", corrects=0)  # 更正后仍是终态
    _order(ledger, "ord_b2", 2, "cancelled")  # 终态后非更正行 → 违规
    found = [f for f in run_checks(ledger, now=_now_after_exit()).findings
             if f.check_id == "A5" and f.order_id == "ord_b2"]
    assert found and "seq=1" in found[0].message


def test_a6_relation_chain_violations(ledger):
    _order(ledger, "ord_skip", 0, "submitted", sid="sig_t2_BBB", ticker="BBB")  # buy on skipped
    _order(ledger, "ord_noexit", 0, "submitted", side="sell")  # sell 无 exit_reason
    _order(ledger, "ord_flip", 0, "submitted", side="sell", exit_reason="direction_flip")  # 无 trigger
    _order(ledger, "ord_nosig", 0, "submitted", sid="sig_missing")
    found = {f.order_id for f in run_checks(ledger, now=_now_after_exit()).findings if f.check_id == "A6"}
    assert {"ord_skip", "ord_noexit", "ord_flip", "ord_nosig"} <= found


def test_a7_timeline_warns(ledger):
    ledger.execute("INSERT INTO signals VALUES ('sig_back', ?, ?, 'followed')",
                   [_CALL, _CALL - timedelta(minutes=1)])  # ingested 早于 call
    _fill(ledger, "fil_early", "ord_1", ts=_SUBMITTED - timedelta(minutes=6), qty=0.1)  # 超 5min 容差
    result = run_checks(ledger, now=_now_after_exit())
    msgs = [f.message for f in result.findings if f.check_id == "A7"]
    assert any("ingested_ts 早于" in m for m in msgs)
    assert any("fill_ts 早于" in m for m in msgs)
    assert result.result != "pass"


def test_a7_within_tolerance_silent(ledger):
    _fill(ledger, "fil_close", "ord_2", ts=_SUBMITTED + timedelta(days=30) - timedelta(minutes=4), qty=0.1)
    assert not [f for f in run_checks(ledger, now=_now_after_exit()).findings
                if f.check_id == "A7" and f.actual == "fill_id=fil_close"]


def test_a8_negative_position_halts(ledger):
    _order(ledger, "ord_early_sell", 0, "filled", side="sell", ticker="CCC", exit_reason="manual")
    _fill(ledger, "fil_early_sell", "ord_early_sell", ts=_CALL, qty=10)  # CCC 从未买入
    found = [f for f in run_checks(ledger, now=_now_after_exit()).findings if f.check_id == "A8"]
    assert found and found[0].ticker == "CCC" and found[0].severity == "halt"


def test_a9_watermark_stale_and_empty(ledger):
    stale_now = _now_after_exit() + timedelta(hours=5)
    found = [f for f in run_checks(ledger, now=stale_now).findings if f.check_id == "A9"]
    assert found and found[0].severity == "warn"
    ledger.execute("DELETE FROM ingest_watermark")  # 测试 fixture 操作,非 ledger 语义
    found = [f for f in run_checks(ledger, now=stale_now).findings if f.check_id == "A9"]
    assert found and "为空" in found[0].message


def test_missing_table_reported_not_crash():
    con = duckdb.connect()
    con.execute(_DDL.replace("CREATE TABLE ingest_watermark (poll_ts TIMESTAMPTZ, "
                             "last_seen_call_ts TIMESTAMPTZ, calls_seen INTEGER);", ""))
    result = run_checks(con, now=_NOW)
    a9 = [f for f in result.findings if f.check_id == "A9"]
    assert result.result == "halt" and a9 and "检查无法执行" in a9[0].message


def test_run_recon_writes_artifacts(tmp_path: Path):
    db = tmp_path / "ledger.duckdb"
    con = duckdb.connect(str(db))
    con.execute(_DDL)
    con.execute("INSERT INTO ingest_watermark VALUES (now(), now(), 0)")
    con.close()
    result = run_recon(db, out_root=tmp_path / "recon")
    out_dir = next((tmp_path / "recon").iterdir())
    assert (out_dir / "findings.json").exists()
    report = (out_dir / "RECON_RESULT.md").read_text(encoding="utf-8")
    assert "未经独立复核" in report
    assert result.result == "pass"  # 空 ledger + 新鲜水位:九项均成立
