"""模拟盘 ledger 取数薄层 — MOCK 回落实现(schema 对齐 ORDER_LEDGER_SPEC r3)。

真实现 = ledger_reader.py(读 Exec 的 parquet 导出);本模块仅在导出尚未上线
(export_meta 不存在)时被页面回落使用,列名/类型与 r3 表/视图一一对应。
r3 对齐:account_daily(total_equity/buying_power)、agent_runs(计数列+error+
可空 finished_ts)、orders +broker_order_ref/+corrects_seq/+expired 终态。
"""

from datetime import UTC, date, datetime, timedelta

import pandas as pd

IS_MOCK = True
# mock 的"parquet 导出时间"(真实实现从 export_meta 读,页面据此自证新鲜度)
EXPORT_TS = datetime(2026, 6, 10, 20, 28, tzinfo=UTC)

_UTC = UTC
_T0 = datetime(2026, 6, 10, tzinfo=_UTC)


def _ts(day_offset: float, hh: int = 0, mm: int = 0) -> datetime:
    return _T0 + timedelta(days=day_offset, hours=hh, minutes=mm)


_TWEET_NVDA_AMD = (
    "Both $NVDA and $AMD are setting up for a monster run into earnings. "
    "Datacenter demand is NOT slowing down. Loading both here."
)
_TWEET_PLTR = "$PLTR new government contract just dropped. This goes higher. Adding."
_TWEET_PLTR_FLIP = "$PLTR run is done imo, taking everything off the table here."
_TWEET_TSLA = "$TSLA robotaxi event is the catalyst everyone is sleeping on. Long."


def load_signals() -> pd.DataFrame:
    """signals 表(r2:signal_id = sig_<tweet_id>_<ticker>,同帖多 ticker 多行)。"""
    cols = [
        "signal_id",
        "tweet_id",
        "handle",
        "author_id",
        "tier",
        "tier_csv_date",
        "ticker",
        "direction",
        "call_ts",
        "ingested_ts",
        "tweet_text",
        "tweet_url",
        "tweet_created_at",
        "tweet_blocked",
        "conviction",
        "confidence",
        "decision",
        "decision_reason",
        "rule_version",
    ]
    rows = [
        # 同一原帖喊两只票 → 两条 signal(r2 场景):一跟一跳
        (
            "sig_1001_NVDA",
            "1001",
            "chipwhisperer",
            "u_11",
            "PROVEN",
            date(2026, 6, 1),
            "NVDA",
            "bullish",
            _ts(-8, 13, 2),
            _ts(-8, 14, 10),
            _TWEET_NVDA_AMD,
            "https://x.com/chipwhisperer/status/1001",
            _ts(-8, 13, 2),
            False,
            "high",
            0.91,
            "followed",
            "all_gates_passed",
            "rules-v1",
        ),
        (
            "sig_1001_AMD",
            "1001",
            "chipwhisperer",
            "u_11",
            "PROVEN",
            date(2026, 6, 1),
            "AMD",
            "bullish",
            _ts(-8, 13, 2),
            _ts(-8, 14, 10),
            _TWEET_NVDA_AMD,
            "https://x.com/chipwhisperer/status/1001",
            _ts(-8, 13, 2),
            False,
            "high",
            0.91,
            "skipped",
            "risk_cap_exceeded",
            "rules-v1",
        ),
        (
            "sig_1002_PLTR",
            "1002",
            "macrodegen",
            "u_22",
            "PROVEN",
            date(2026, 6, 2),
            "PLTR",
            "bullish",
            _ts(-7, 15, 40),
            _ts(-7, 17, 5),
            _TWEET_PLTR,
            "https://x.com/macrodegen/status/1002",
            _ts(-7, 15, 40),
            False,
            "medium",
            0.74,
            "followed",
            "all_gates_passed",
            "rules-v1",
        ),
        # 同 handle 同票反向喊单 → 退出触发(decision=skipped/exit_trigger)
        (
            "sig_1003_PLTR",
            "1003",
            "macrodegen",
            "u_22",
            "PROVEN",
            date(2026, 6, 8),
            "PLTR",
            "bearish",
            _ts(-1, 14, 20),
            _ts(-1, 15, 45),
            _TWEET_PLTR_FLIP,
            "https://x.com/macrodegen/status/1003",
            _ts(-1, 14, 20),
            False,
            "medium",
            0.68,
            "skipped",
            "exit_trigger",
            "rules-v1",
        ),
        (
            "sig_1004_TSLA",
            "1004",
            "chipwhisperer",
            "u_11",
            "PROVEN",
            date(2026, 6, 9),
            "TSLA",
            "bullish",
            _ts(0, 13, 55),
            _ts(0, 15, 12),
            _TWEET_TSLA,
            "https://x.com/chipwhisperer/status/1004",
            _ts(0, 13, 55),
            False,
            "low",
            0.55,
            "followed",
            "all_gates_passed",
            "rules-v1",
        ),
        # blocked 原帖:正文禁止对外展示(spec §9 Dash 会签项)
        (
            "sig_1005_GME",
            "1005",
            "yolo_oracle",
            "u_33",
            "PROVEN",
            date(2026, 6, 9),
            "GME",
            "bullish",
            _ts(0, 14, 30),
            _ts(0, 15, 50),
            "[BLOCKED CONTENT]",
            "https://x.com/yolo_oracle/status/1005",
            _ts(0, 14, 30),
            True,
            None,
            None,
            "skipped",
            "manual_block",
            "rules-v1",
        ),
    ]
    return pd.DataFrame(rows, columns=cols)


def load_orders_current() -> pd.DataFrame:
    """v_orders_current 视图(每订单事件流最新一行)。"""
    cols = [
        "order_id",
        "seq",
        "event_ts",
        "signal_id",
        "ticker",
        "side",
        "qty",
        "order_type",
        "limit_price",
        "submitted_ts",
        "call_to_submit_ms",
        "status",
        "rule_version",
        "kill_switch_engaged",
        "exit_reason",
        "exit_trigger_signal_id",
        "note",
        "broker_order_ref",
        "corrects_seq",
    ]
    rows = [
        (
            "ord_01J100",
            2,
            _ts(-8, 14, 25),
            "sig_1001_NVDA",
            "NVDA",
            "buy",
            8.0,
            "market",
            None,
            _ts(-8, 14, 12),
            4_200_000,
            "filled",
            "rules-v1",
            False,
            None,
            None,
            None,
            "FT-90021",
            None,
        ),
        (
            "ord_01J200",
            1,
            _ts(-7, 17, 20),
            "sig_1002_PLTR",
            "PLTR",
            "buy",
            12.0,
            "market",
            None,
            _ts(-7, 17, 8),
            5_280_000,
            "filled",
            "rules-v1",
            False,
            None,
            None,
            None,
            "FT-90043",
            None,
        ),
        # direction_flip 平仓单:signal_id 指向原入场信号,exit_trigger 指向反向喊单
        (
            "ord_01J300",
            1,
            _ts(-1, 16, 2),
            "sig_1002_PLTR",
            "PLTR",
            "sell",
            12.0,
            "market",
            None,
            _ts(-1, 15, 50),
            5_400_000,
            "filled",
            "rules-v1",
            False,
            "direction_flip",
            "sig_1003_PLTR",
            None,
            "FT-90105",
            None,
        ),
        # 今日新单:已提交未成交
        (
            "ord_01J400",
            0,
            _ts(0, 15, 20),
            "sig_1004_TSLA",
            "TSLA",
            "buy",
            5.0,
            "limit",
            312.50,
            _ts(0, 15, 20),
            5_100_000,
            "submitted",
            "rules-v1",
            False,
            None,
            None,
            None,
            "FT-90112",
            None,
        ),
    ]
    return pd.DataFrame(rows, columns=cols)


def load_order_events(order_id: str) -> pd.DataFrame:
    """orders 事件流(单订单全部 seq,留档页展示状态机轨迹)。"""
    cur = load_orders_current()
    row = cur[cur["order_id"] == order_id]
    if row.empty:
        return row
    r = row.iloc[0]
    events = []
    # mock:按最新 seq 反推事件链(真实实现直接 SELECT * WHERE order_id ORDER BY seq)
    chain = {0: ["submitted"], 1: ["submitted", r["status"]], 2: ["submitted", "partial", r["status"]]}[int(r["seq"])]
    for i, status in enumerate(chain):
        e = r.copy()
        e["seq"], e["status"] = i, status
        e["event_ts"] = r["event_ts"] - timedelta(minutes=(len(chain) - 1 - i) * 6)
        events.append(e)
    return pd.DataFrame(events)


def load_fills_effective() -> pd.DataFrame:
    """v_fills_effective(已剔除作废对)。"""
    cols = ["fill_id", "order_id", "fill_ts", "qty", "price", "raw_text", "scraped_ts", "voids_fill_id", "note"]
    rows = [
        (
            "fil_01A",
            "ord_01J100",
            _ts(-8, 14, 18),
            5.0,
            176.42,
            "06/02/26 10:18 ET BOT 5 NVDA @ 176.42 PAPER",
            _ts(-8, 14, 19),
            None,
            None,
        ),
        (
            "fil_01B",
            "ord_01J100",
            _ts(-8, 14, 24),
            3.0,
            176.55,
            "06/02/26 10:24 ET BOT 3 NVDA @ 176.55 PAPER",
            _ts(-8, 14, 25),
            None,
            None,
        ),
        (
            "fil_02A",
            "ord_01J200",
            _ts(-7, 17, 15),
            12.0,
            138.10,
            "06/03/26 13:15 ET BOT 12 PLTR @ 138.10 PAPER",
            _ts(-7, 17, 16),
            None,
            None,
        ),
        (
            "fil_03A",
            "ord_01J300",
            _ts(-1, 15, 58),
            12.0,
            151.84,
            "06/09/26 11:58 ET SLD 12 PLTR @ 151.84 PAPER",
            _ts(-1, 15, 59),
            None,
            None,
        ),
    ]
    return pd.DataFrame(rows, columns=cols)


def load_order_filled() -> pd.DataFrame:
    """v_order_filled 视图(每订单成交聚合)。"""
    f = load_fills_effective()
    g = (
        f.assign(amt=f["qty"] * f["price"])
        .groupby("order_id")
        .agg(
            filled_qty=("qty", "sum"),
            amt=("amt", "sum"),
            first_fill_ts=("fill_ts", "min"),
            last_fill_ts=("fill_ts", "max"),
            n_fills=("fill_id", "count"),
        )
        .reset_index()
    )
    g["avg_fill_price"] = g["amt"] / g["filled_qty"]
    return g.drop(columns=["amt"])


def load_positions_eod(days: int = 10) -> pd.DataFrame:
    """v_positions_eod(逐日每票最终快照)。"""
    cols = ["snapshot_date", "snapshot_ts", "ticker", "qty", "avg_cost", "close", "unrealized_pnl", "raw_text"]
    nvda_close = [176.5, 177.8, 175.9, 179.2, 181.0, 180.4, 183.1, 184.0]
    pltr_close = [138.4, 140.2, 139.0, 143.5, 146.8, 149.9]  # 06-09 盘中已平,无其后 EOD 行
    rows = []
    for i, c in enumerate(nvda_close):  # 06-03 .. 06-10
        d = (_T0 - timedelta(days=7 - i)).date()
        rows.append(
            (d, _ts(i - 7, 21, 5), "NVDA", 8.0, 176.47, c, round((c - 176.47) * 8, 2), "mock holdings page text")
        )
    for i, c in enumerate(pltr_close):  # 06-03 .. 06-08
        d = (_T0 - timedelta(days=7 - i)).date()
        rows.append(
            (d, _ts(i - 7, 21, 5), "PLTR", 12.0, 138.10, c, round((c - 138.10) * 12, 2), "mock holdings page text")
        )
    return pd.DataFrame(rows, columns=cols)


def load_pdt_latest() -> pd.DataFrame:
    """v_pdt_latest(最新簿记快照,闸门/监控读这里)。"""
    return pd.DataFrame(
        [
            {
                "entry_id": "pdt_01X9",
                "event_ts": _ts(0, 15, 20),
                "trade_date": date(2026, 6, 10),
                "event_type": "cash_debit",
                "ticker": "TSLA",
                "order_id": "ord_01J400",
                "cash_delta": -1562.50,
                "settle_date": None,
                "day_trades_5d": 1,
                "settled_cash": 8254.30,
                "note": None,
            }
        ]
    )


def load_ingest_watermark_latest() -> pd.DataFrame:
    """ingest_watermark 最新一轮(信号管线新鲜度)。"""
    return pd.DataFrame(
        [
            {
                "poll_ts": _ts(0, 20, 25),
                "last_seen_call_ts": _ts(0, 14, 30),
                "calls_seen": 3,
                "note": None,
            }
        ]
    )


def load_recon_status() -> pd.DataFrame:
    """§7 对账:最近 N 日 eod_snapshot 的 recon 结果(从 pdt_ledger note 解析)。"""
    rows = [{"trade_date": (_T0 - timedelta(days=i + 1)).date(), "recon": "ok"} for i in range(7)]
    return pd.DataFrame(rows).sort_values("trade_date")


# ---------- r3 §4.5b 两表(Dash 会签新提,已定稿入 spec)----------


def load_account_daily(days: int = 10) -> pd.DataFrame:
    """account_daily(r3):账户级每日快照,权益曲线落点;同日重抓取最新。"""
    equity = [10000.0, 10004.1, 10042.6, 10018.9, 10101.3, 10160.2, 10148.7, 10241.5, 10287.9, 10295.4]
    rows = [
        {
            "snapshot_date": (_T0 - timedelta(days=len(equity) - 1 - i)).date(),
            "snapshot_ts": _ts(i - len(equity) + 1, 21, 6),
            "total_equity": v,
            "cash": round(v - (1411.8 if i < 7 else 1472.0), 2),
            "buying_power": round((v - (1411.8 if i < 7 else 1472.0)) * 2, 2),
            "raw_text": "mock balances page text",
        }
        for i, v in enumerate(equity)
    ]
    return pd.DataFrame(rows)


def load_agent_runs(n: int = 20) -> pd.DataFrame:
    """agent_runs(r3):每轮执行循环心跳;finished_ts 为 NULL = 崩溃未收尾。"""
    rows = []
    for i in range(n):
        ok = i != 7  # mock:第 7 轮一次登录态过期错误
        rows.append(
            {
                "run_id": f"run_{1000 + n - i}",
                "started_ts": _ts(0, 20, 28) - timedelta(minutes=15 * i),
                "finished_ts": _ts(0, 20, 30) - timedelta(minutes=15 * i),
                "kill_switch": False,
                "signals_seen": 1 if i in (3, 11) else 0,
                "orders_placed": 1 if i == 3 else 0,
                "fills_scraped": 1 if i == 2 else 0,
                "export_ok": ok,
                "error": None if ok else "login session expired; re-auth + back off",
                "note": None,
            }
        )
    return pd.DataFrame(rows)
