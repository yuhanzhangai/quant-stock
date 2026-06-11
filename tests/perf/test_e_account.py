"""E 账测试:合成三库(ledger duckdb / price_cache sqlite / call_outcomes sqlite)钉死收益与归因算术。

三笔跟单:① 已平仓 hold_21d(S evaluated,全套归因)② 已平仓 stop_loss(early_exit_diff 走 hold21 日历)
③ 未平仓(mark-to-market 单列)。所有期望值手算。
"""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest

from src.perf.e_account import enrich, hold21_session, load_prices, load_s_rows, load_trades, run

_DDL = """
CREATE TABLE signals (signal_id TEXT PRIMARY KEY, tweet_id TEXT, ticker TEXT, handle TEXT,
    call_ts TIMESTAMPTZ, ingested_ts TIMESTAMPTZ);
CREATE TABLE orders (order_id TEXT, seq INTEGER, signal_id TEXT, ticker TEXT, side TEXT,
    qty DECIMAL(18,4), submitted_ts TIMESTAMPTZ, call_to_submit_ms BIGINT, exit_reason TEXT,
    status TEXT, PRIMARY KEY (order_id, seq));
CREATE TABLE fills (fill_id TEXT PRIMARY KEY, order_id TEXT, fill_ts TIMESTAMPTZ,
    qty DECIMAL(18,4), price DECIMAL(18,4), voids_fill_id TEXT);
CREATE VIEW v_orders_current AS SELECT * FROM orders
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY seq DESC) = 1;
CREATE VIEW v_fills_effective AS SELECT * FROM fills f
WHERE f.voids_fill_id IS NULL AND NOT EXISTS (SELECT 1 FROM fills v WHERE v.voids_fill_id = f.fill_id);
CREATE VIEW v_order_filled AS SELECT order_id, sum(qty) AS filled_qty,
    sum(qty * price) / nullif(sum(qty), 0) AS avg_fill_price, min(fill_ts) AS first_fill_ts,
    max(fill_ts) AS last_fill_ts, count(*) AS n_fills FROM v_fills_effective GROUP BY order_id;
"""

# 美东 14:00 = UTC 18:00(EDT);entry_date 即 fill 当日
_T1_ENTRY = datetime(2026, 4, 6, 18, 0, tzinfo=UTC)   # Mon
_T1_EXIT = datetime(2026, 5, 5, 18, 0, tzinfo=UTC)    # Tue
_T2_ENTRY = datetime(2026, 1, 5, 18, 0, tzinfo=UTC)   # Mon
_T2_EXIT = datetime(2026, 1, 15, 18, 0, tzinfo=UTC)   # Thu(止损早退)
_T3_ENTRY = datetime(2026, 6, 1, 18, 0, tzinfo=UTC)   # Mon,未平仓

_PRICES = [
    # AAA:E 窗 100→110(+10%);S 窗(04-07→05-06)100→105(+5%)→ window_diff=+5%
    ("AAA", "2026-04-06", 100.0), ("AAA", "2026-05-05", 110.0),
    ("AAA", "2026-04-07", 100.0), ("AAA", "2026-05-06", 105.0),
    # SPY:T1 窗 500→510(+2%);T2 窗 500→505(+1%)
    ("SPY", "2026-04-06", 500.0), ("SPY", "2026-05-05", 510.0),
    ("SPY", "2026-01-05", 500.0), ("SPY", "2026-01-15", 505.0),
    # BBB:entry 50 / 早退日 46 / hold21(2026-02-04)55 → early_exit_diff=(46-55)/50=-18%
    ("BBB", "2026-01-05", 50.0), ("BBB", "2026-01-15", 46.0), ("BBB", "2026-02-04", 55.0),
    # CCC:未平仓,最新收盘 120
    ("CCC", "2026-06-01", 100.0), ("CCC", "2026-06-10", 120.0),
    # DDD:复权嫌疑(NVDA 式 10:1)——fill 按当时真价 1149,缓存被后向复权回填成 114.9
    ("DDD", "2026-01-05", 114.9), ("DDD", "2026-01-15", 120.0),
    ("SPY", "2026-06-01", 700.0),
]


@pytest.fixture()
def dbs(tmp_path: Path) -> tuple[Path, Path, Path]:
    ledger = tmp_path / "ledger.duckdb"
    con = duckdb.connect(str(ledger))
    con.execute(_DDL)
    for sid, tid, tk in [("sig_t1_AAA", "t1", "AAA"), ("sig_t2_BBB", "t2", "BBB"),
                         ("sig_t3_CCC", "t3", "CCC"), ("sig_t4_DDD", "t4", "DDD")]:
        con.execute("INSERT INTO signals VALUES (?,?,?,?,?,?)",
                    [sid, tid, tk, "shay", _T2_ENTRY, _T2_ENTRY])
    legs = [  # (oid, sid, tk, side, qty, fill_ts, avg, call_to_submit_ms, exit_reason)
        ("ord_1b", "sig_t1_AAA", "AAA", "buy", 10, _T1_ENTRY, 100.0, 3_600_000, None),
        ("ord_1s", "sig_t1_AAA", "AAA", "sell", 10, _T1_EXIT, 110.0, None, "hold_21d"),
        ("ord_2b", "sig_t2_BBB", "BBB", "buy", 20, _T2_ENTRY, 50.0, 90_000_000, None),  # 25h → 1–3d 桶
        ("ord_2s", "sig_t2_BBB", "BBB", "sell", 20, _T2_EXIT, 46.0, None, "stop_loss"),
        ("ord_3b", "sig_t3_CCC", "CCC", "buy", 5, _T3_ENTRY, 100.0, 3_600_000, None),
        # 复权嫌疑:历史回填按拆股前真价成交,price_cache 同日收盘已是复权价(差 10 倍)
        ("ord_4b", "sig_t4_DDD", "DDD", "buy", 4, _T2_ENTRY, 1149.0, 3_600_000, None),
        ("ord_4s", "sig_t4_DDD", "DDD", "sell", 4, _T2_EXIT, 120.0, None, "hold_21d"),
    ]
    for oid, sid, tk, side, qty, ts, avg, ms, reason in legs:
        con.execute("INSERT INTO orders VALUES (?,0,?,?,?,?,?,?,?, 'filled')",
                    [oid, sid, tk, side, qty, ts, ms, reason])
        con.execute("INSERT INTO fills VALUES (?,?,?,?,?,NULL)", [f"fil_{oid}", oid, ts, qty, avg])
    con.close()

    prices = tmp_path / "prices.db"
    pc = sqlite3.connect(prices)
    pc.execute("CREATE TABLE price_cache (ticker TEXT, date TEXT, close REAL, fetched_at INTEGER)")
    pc.executemany("INSERT INTO price_cache VALUES (?,?,?,0)", _PRICES)
    pc.commit()
    pc.close()

    tr = tmp_path / "tr.db"
    tc = sqlite3.connect(tr)
    tc.execute("CREATE TABLE call_outcomes (tweet_id TEXT, ticker TEXT, horizon_days INTEGER,"
               "entry_date TEXT, entry_close REAL, exit_date TEXT, exit_close REAL,"
               "abnormal_return REAL, status TEXT)")
    # T1 的 S 行:entry_close=100 → entry_diff_bps=0;abnormal +5%
    tc.execute("INSERT INTO call_outcomes VALUES ('t1','AAA',21,'2026-04-07',100,'2026-05-06',105,0.05,'evaluated')")
    tc.execute("INSERT INTO call_outcomes VALUES ('t2','BBB',21,'2026-01-06',50,'2026-02-04',55,0.10,'evaluated')")
    # t3 无行(pending 等价:不归因)
    tc.commit()
    tc.close()
    return ledger, prices, tr


def _enriched(dbs):
    ledger, prices_db, tr = dbs
    trades = load_trades(ledger)
    prices = load_prices(sorted({*trades["ticker"].to_list(), "SPY"}), prices_db)
    s_rows = load_s_rows([(t["tweet_id"], t["ticker"]) for t in trades.iter_rows(named=True)], tr)
    return enrich(trades, prices, s_rows)


def test_hold21_session_known_value():
    # 2026-01-05(周一)后第 21 个 NYSE 交易日 = 2026-02-04(MLK 1/19 闭市)
    assert hold21_session(date(2026, 1, 5)) == date(2026, 2, 4)


def test_closed_trade_returns_and_attribution(dbs):
    df = _enriched(dbs)
    t1 = df.filter(df["signal_id"] == "sig_t1_AAA").row(0, named=True)
    assert t1["closed"] and t1["entry_date"] == date(2026, 4, 6)
    assert t1["actual_return"] == pytest.approx(0.10)
    assert t1["spy_return"] == pytest.approx(0.02)
    assert t1["actual_abnormal"] == pytest.approx(0.08)
    assert t1["entry_diff_bps"] == pytest.approx(0.0)
    assert t1["window_diff"] == pytest.approx(0.10 - 0.05)
    assert t1["early_exit_diff"] == 0.0  # hold_21d 按定义为 0
    assert t1["se_gap"] == pytest.approx(0.05 - 0.08)
    assert t1["wall_bucket"] == "≤2h"


def test_stop_loss_early_exit_diff(dbs):
    t2 = _enriched(dbs).filter(pl.col("signal_id") == "sig_t2_BBB").row(0, named=True)
    assert t2["actual_return"] == pytest.approx(46 / 50 - 1)
    assert t2["early_exit_diff"] == pytest.approx((46 - 55) / 50)  # 早退 vs 持满 21d 的代价
    assert t2["wall_bucket"] == "1–3d"


def test_open_trade_mtm_isolated(dbs):
    t3 = _enriched(dbs).filter(pl.col("signal_id") == "sig_t3_CCC").row(0, named=True)
    assert not t3["closed"]
    assert t3["actual_return"] is None and t3["se_gap"] is None
    assert t3["unrealized_return"] == pytest.approx(0.20)
    assert t3["mtm_asof"] == "2026-06-10"


def test_split_suspect_flagged_and_nulled(dbs):
    """PRICE_SOURCE_SPEC §4b 缓解 (b):fill×缓存混锚的笔列异常,收益置空不进聚合;干净笔不受影响。"""
    df = _enriched(dbs)
    t4 = df.filter(pl.col("signal_id") == "sig_t4_DDD").row(0, named=True)
    assert t4["split_suspect"] is True
    assert t4["actual_return"] is None and t4["actual_abnormal"] is None and t4["se_gap"] is None
    t1 = df.filter(pl.col("signal_id") == "sig_t1_AAA").row(0, named=True)
    assert t1["split_suspect"] is False and t1["actual_return"] == pytest.approx(0.10)


def test_run_writes_artifacts(dbs, tmp_path: Path):
    ledger, prices_db, tr = dbs
    out = run(ledger, out_root=tmp_path / "fp", prices_db=prices_db, trackrecord_db=tr)
    report = (out / "E_ACCOUNT_REPORT.md").read_text(encoding="utf-8")
    assert (out / "e_account.parquet").exists()
    assert "未经独立复核" in report and "stop_loss" in report and "未平仓" in report
