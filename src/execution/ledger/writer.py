"""Ledger writer:insert-only API(spec §2),状态机白名单含 correction 分支(§5.2/§5.3),
fills 幂等查重(§4.3),单写者 = 执行循环。

纪律:
- 本封装不暴露 UPDATE/DELETE。唯一例外是 ``finish_agent_run``:agent_runs 是心跳表
  (非审计链路),spec §4.5b 要求"崩溃则 finished_ts 为 NULL,本身即证据",在
  run_id 单行主键下只能由一次定向回填实现——spec r3.1 已将其定为显式例外
  (仅限未收尾行;资金六表零 UPDATE 不变)。
- 非法状态迁移拒写并告警,不静默落账(§4.2)。
- kill-switch 不阻断记账(会签约定):本模块任何方法都不查 kill 状态。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from loguru import logger

from src.execution.ledger.ids import new_fill_id, new_order_id, new_pdt_entry_id, new_run_id

SCHEMA_VERSION = 1
_SCHEMA_SQL = Path(__file__).parent / "schema.sql"

# §5.2 状态迁移白名单(终态后唯一豁免=更正行,单列处理,不走本表)
_TRANSITIONS: dict[str, frozenset[str]] = {
    "submitted": frozenset({"partial", "filled", "cancelled", "rejected", "expired"}),
    "partial": frozenset({"filled", "cancelled", "expired"}),
}
_TERMINAL: frozenset[str] = frozenset({"filled", "cancelled", "rejected", "expired"})

_ORDER_COLUMNS = (
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
    "broker_order_ref",
    "status",
    "corrects_seq",
    "rule_version",
    "kill_switch_engaged",
    "exit_reason",
    "exit_trigger_signal_id",
    "note",
)
# 事件行之间原样复制的不可变字段(§4.2:单行自含可读)
_ORDER_IMMUTABLE = (
    "signal_id",
    "ticker",
    "side",
    "qty",
    "order_type",
    "limit_price",
    "submitted_ts",
    "call_to_submit_ms",
    "rule_version",
)


class LedgerWriteError(ValueError):
    """非法写入(状态机违规/缺必填/引用不存在)。调用方不得吞掉静默继续。"""


def _now() -> datetime:
    return datetime.now(UTC)


class LedgerWriter:
    """ledger.duckdb 的唯一写入方。所有公开方法均为 insert 语义(agent_runs 例外见模块注释)。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(db_path))
        self._apply_schema()
        logger.info("ledger writer 已连接: {}", db_path)

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """只给导出/测试用;业务写入一律走 insert_* API。"""
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LedgerWriter:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _apply_schema(self) -> None:
        self._conn.execute(_SCHEMA_SQL.read_text(encoding="utf-8"))
        applied = self._conn.execute(
            "SELECT count(*) FROM ledger_meta WHERE schema_version = ?", [SCHEMA_VERSION]
        ).fetchone()[0]
        if applied == 0:
            self._conn.execute(
                "INSERT INTO ledger_meta (schema_version, note) VALUES (?, ?)",
                [SCHEMA_VERSION, "ORDER_LEDGER_SPEC r3"],
            )

    # ── signals(§4.1,不可变;signal_id 幂等防重)─────────────────────────

    def insert_signal(
        self,
        *,
        signal_id: str,
        tweet_id: str,
        handle: str,
        tier: str,
        tier_csv_date: date,
        ticker: str,
        direction: str,
        call_ts: datetime,
        tweet_text: str,
        tweet_url: str,
        decision: str,
        decision_reason: str,
        rule_version: str,
        author_id: str | None = None,
        tweet_created_at: datetime | None = None,
        tweet_blocked: bool = False,
        conviction: str | None = None,
        confidence: float | None = None,
        ingested_ts: datetime | None = None,
    ) -> bool:
        """收录一条喊单。已存在(同 signal_id)则跳过返回 False——轮询重看同一喊单是常态。"""
        exists = self._conn.execute("SELECT count(*) FROM signals WHERE signal_id = ?", [signal_id]).fetchone()[0]
        if exists:
            logger.debug("signal 已收录,跳过: {}", signal_id)
            return False
        self._conn.execute(
            """
            INSERT INTO signals (
                signal_id, tweet_id, handle, author_id, tier, tier_csv_date, ticker,
                direction, call_ts, ingested_ts, tweet_text, tweet_url, tweet_created_at,
                tweet_blocked, conviction, confidence, decision, decision_reason, rule_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                signal_id,
                tweet_id,
                handle,
                author_id,
                tier,
                tier_csv_date,
                ticker,
                direction,
                call_ts,
                ingested_ts or _now(),
                tweet_text,
                tweet_url,
                tweet_created_at,
                tweet_blocked,
                conviction,
                confidence,
                decision,
                decision_reason,
                rule_version,
            ],
        )
        logger.info("signal 收录: {} {} {} decision={}", signal_id, handle, ticker, decision)
        return True

    # ── orders(§4.2,事件溯源)──────────────────────────────────────────

    def _latest_order_row(self, order_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_ORDER_COLUMNS)} FROM orders WHERE order_id = ? ORDER BY seq DESC LIMIT 1",
            [order_id],
        ).fetchone()
        return dict(zip(_ORDER_COLUMNS, row, strict=True)) if row else None

    def _insert_order_row(self, values: dict[str, Any]) -> None:
        cols = ", ".join(_ORDER_COLUMNS)
        marks = ", ".join("?" for _ in _ORDER_COLUMNS)
        self._conn.execute(
            f"INSERT INTO orders ({cols}) VALUES ({marks})",
            [values[c] for c in _ORDER_COLUMNS],
        )

    @staticmethod
    def _check_exit_consistency(exit_reason: str | None, kill_switch_engaged: bool) -> None:
        # spec §6 一致性约束:exit_reason='kill_switch' ⇒ kill_switch_engaged=TRUE
        if exit_reason == "kill_switch" and not kill_switch_engaged:
            raise LedgerWriteError("exit_reason='kill_switch' 时 kill_switch_engaged 必须为 TRUE")

    def open_order(
        self,
        *,
        signal_id: str,
        ticker: str,
        side: str,
        qty: Decimal,
        order_type: str,
        submitted_ts: datetime,
        rule_version: str,
        order_id: str | None = None,
        limit_price: Decimal | None = None,
        call_to_submit_ms: int | None = None,
        broker_order_ref: str | None = None,
        kill_switch_engaged: bool = False,
        exit_reason: str | None = None,
        exit_trigger_signal_id: str | None = None,
        event_ts: datetime | None = None,
        note: str | None = None,
    ) -> str:
        """seq=0 落账(status='submitted')。

        落账时序约定(Exec 会签②):提交点击成功后立即调用,不等确认页解析——
        确认未读到时 broker_order_ref 留空、note 标 confirmation_unverified,回采后追加事件行补记。
        """
        order_id = order_id or new_order_id()
        if self._latest_order_row(order_id) is not None:
            raise LedgerWriteError(f"order_id 已存在,seq=0 不可重复落账: {order_id}")
        self._check_exit_consistency(exit_reason, kill_switch_engaged)
        self._insert_order_row(
            {
                "order_id": order_id,
                "seq": 0,
                "event_ts": event_ts or _now(),
                "signal_id": signal_id,
                "ticker": ticker,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "limit_price": limit_price,
                "submitted_ts": submitted_ts,
                "call_to_submit_ms": call_to_submit_ms,
                "broker_order_ref": broker_order_ref,
                "status": "submitted",
                "corrects_seq": None,
                "rule_version": rule_version,
                "kill_switch_engaged": kill_switch_engaged,
                "exit_reason": exit_reason,
                "exit_trigger_signal_id": exit_trigger_signal_id,
                "note": note,
            }
        )
        logger.info("order 开账: {} {} {} qty={} status=submitted", order_id, side, ticker, qty)
        return order_id

    def append_order_event(
        self,
        *,
        order_id: str,
        status: str,
        corrects_seq: int | None = None,
        broker_order_ref: str | None = None,
        kill_switch_engaged: bool = False,
        exit_reason: str | None = None,
        exit_trigger_signal_id: str | None = None,
        event_ts: datetime | None = None,
        note: str | None = None,
    ) -> int:
        """追加一次状态事件(seq+1),不可变字段自最新行复制。

        - 正常事件(corrects_seq=None):校验 §5.2 迁移白名单,终态后拒写。
        - 更正行(corrects_seq 非空):终态封锁唯一豁免(§5.3),note 必填,
          corrects_seq 必须指向本订单已存在的 seq;status 写更正后的正确值。
        返回新事件的 seq。
        """
        latest = self._latest_order_row(order_id)
        if latest is None:
            raise LedgerWriteError(f"订单不存在,不能追加事件: {order_id}")

        if corrects_seq is None:
            allowed = _TRANSITIONS.get(latest["status"], frozenset())
            if status not in allowed:
                msg = (
                    f"非法状态迁移拒写: {order_id} {latest['status']} → {status}"
                    f"(白名单: {sorted(allowed) or '无(终态)'})"
                )
                logger.error(msg)
                raise LedgerWriteError(msg)
        else:
            if not note:
                raise LedgerWriteError(f"更正行 note 必填缘由: {order_id} corrects_seq={corrects_seq}")
            if not 0 <= corrects_seq <= latest["seq"]:
                raise LedgerWriteError(
                    f"corrects_seq 必须指向本订单已存在的事件: {order_id} "
                    f"corrects_seq={corrects_seq},当前最大 seq={latest['seq']}"
                )
            logger.warning(
                "order 更正行: {} corrects_seq={} status→{} note={}",
                order_id,
                corrects_seq,
                status,
                note,
            )

        self._check_exit_consistency(exit_reason, kill_switch_engaged)
        new_seq = latest["seq"] + 1
        values = {c: latest[c] for c in _ORDER_IMMUTABLE}
        values.update(
            {
                "order_id": order_id,
                "seq": new_seq,
                "event_ts": event_ts or _now(),
                # 回采可能晚于 seq=0 才读到券商订单号:新值优先,否则沿用已记录值
                "broker_order_ref": broker_order_ref or latest["broker_order_ref"],
                "status": status,
                "corrects_seq": corrects_seq,
                "kill_switch_engaged": kill_switch_engaged,
                "exit_reason": exit_reason if exit_reason is not None else latest["exit_reason"],
                "exit_trigger_signal_id": exit_trigger_signal_id
                if exit_trigger_signal_id is not None
                else latest["exit_trigger_signal_id"],
                "note": note,
            }
        )
        self._insert_order_row(values)
        logger.info("order 事件: {} seq={} status={}", order_id, new_seq, status)
        return new_seq

    def order_current(self, order_id: str) -> dict[str, Any] | None:
        """订单现状(=事件流最新行),回采/闸门用的轻量读。"""
        return self._latest_order_row(order_id)

    # ── fills(§4.3,不可变追加 + 幂等查重)──────────────────────────────

    def insert_fill(
        self,
        *,
        order_id: str,
        fill_ts: datetime,
        qty: Decimal,
        price: Decimal,
        raw_text: str,
        fill_id: str | None = None,
        scraped_ts: datetime | None = None,
        note: str | None = None,
    ) -> str | None:
        """落一笔成交回采。幂等纪律(r3 Exec②):自然键 (order_id, fill_ts, qty, price)
        已存在于 v_fills_effective 则跳过返回 None——回采是轮询型,同页会被反复读。"""
        if self._latest_order_row(order_id) is None:
            raise LedgerWriteError(f"fills 逻辑外键校验失败,订单不存在: {order_id}")
        dup = self._conn.execute(
            "SELECT count(*) FROM v_fills_effective WHERE order_id = ? AND fill_ts = ? AND qty = ? AND price = ?",
            [order_id, fill_ts, qty, price],
        ).fetchone()[0]
        if dup:
            logger.debug("fill 幂等跳过(已落账): {} {} qty={} price={}", order_id, fill_ts, qty, price)
            return None
        fill_id = fill_id or new_fill_id()
        self._conn.execute(
            """
            INSERT INTO fills (fill_id, order_id, fill_ts, qty, price, raw_text,
                               scraped_ts, voids_fill_id, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            [fill_id, order_id, fill_ts, qty, price, raw_text, scraped_ts or _now(), note],
        )
        logger.info("fill 落账: {} order={} qty={} price={}", fill_id, order_id, qty, price)
        return fill_id

    def void_fill(self, *, fill_id: str, note: str) -> str:
        """作废一笔回采错误的 fill(§5.3):追加 voids_fill_id 指向原行的作废行,
        其余字段复制原行;v_fills_effective 自动剔除作废对。正确值由调用方另插新 fill。"""
        if not note:
            raise LedgerWriteError("作废行 note 必填缘由")
        row = self._conn.execute(
            "SELECT order_id, fill_ts, qty, price, raw_text, voids_fill_id FROM fills WHERE fill_id = ?",
            [fill_id],
        ).fetchone()
        if row is None:
            raise LedgerWriteError(f"待作废 fill 不存在: {fill_id}")
        order_id, fill_ts, qty, price, raw_text, voids = row
        if voids is not None:
            raise LedgerWriteError(f"作废行本身不可再被作废: {fill_id}")
        already = self._conn.execute("SELECT count(*) FROM fills WHERE voids_fill_id = ?", [fill_id]).fetchone()[0]
        if already:
            raise LedgerWriteError(f"fill 已被作废过: {fill_id}")
        void_id = new_fill_id()
        self._conn.execute(
            """
            INSERT INTO fills (fill_id, order_id, fill_ts, qty, price, raw_text,
                               scraped_ts, voids_fill_id, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [void_id, order_id, fill_ts, qty, price, raw_text, _now(), fill_id, note],
        )
        logger.warning("fill 作废: {} voids {} note={}", void_id, fill_id, note)
        return void_id

    # ── positions_daily(§4.4)/ account_daily(§4.5b)────────────────────

    def insert_position_snapshot(
        self,
        *,
        snapshot_date: date,
        ticker: str,
        qty: Decimal,
        raw_text: str,  # 列可空,但 writer 必填(Exec 会签承诺:对账锚点必须有原件)
        avg_cost: Decimal | None = None,
        close: Decimal | None = None,
        unrealized_pnl: Decimal | None = None,
        snapshot_ts: datetime | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO positions_daily (snapshot_date, snapshot_ts, ticker, qty,
                                         avg_cost, close, unrealized_pnl, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [snapshot_date, snapshot_ts or _now(), ticker, qty, avg_cost, close, unrealized_pnl, raw_text],
        )

    def insert_account_snapshot(
        self,
        *,
        snapshot_date: date,
        total_equity: Decimal,
        raw_text: str,
        cash: Decimal | None = None,
        buying_power: Decimal | None = None,
        snapshot_ts: datetime | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO account_daily (snapshot_date, snapshot_ts, total_equity,
                                       cash, buying_power, raw_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [snapshot_date, snapshot_ts or _now(), total_equity, cash, buying_power, raw_text],
        )

    # ── pdt_ledger(§4.5)────────────────────────────────────────────────

    def insert_pdt_entry(
        self,
        *,
        trade_date: date,
        event_type: str,
        day_trades_5d: int,
        settled_cash: Decimal,
        entry_id: str | None = None,
        ticker: str | None = None,
        order_id: str | None = None,
        cash_delta: Decimal | None = None,
        settle_date: date | None = None,
        event_ts: datetime | None = None,
        note: str | None = None,
    ) -> str:
        if order_id is not None and self._latest_order_row(order_id) is None:
            raise LedgerWriteError(f"pdt_ledger 逻辑外键校验失败,订单不存在: {order_id}")
        entry_id = entry_id or new_pdt_entry_id()
        self._conn.execute(
            """
            INSERT INTO pdt_ledger (entry_id, event_ts, trade_date, event_type, ticker,
                                    order_id, cash_delta, settle_date, day_trades_5d,
                                    settled_cash, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entry_id,
                event_ts or _now(),
                trade_date,
                event_type,
                ticker,
                order_id,
                cash_delta,
                settle_date,
                day_trades_5d,
                settled_cash,
                note,
            ],
        )
        return entry_id

    # ── agent_runs(§4.5b,心跳)─────────────────────────────────────────

    def start_agent_run(
        self,
        *,
        kill_switch: bool,
        run_id: str | None = None,
        started_ts: datetime | None = None,
        note: str | None = None,
    ) -> str:
        run_id = run_id or new_run_id()
        self._conn.execute(
            """
            INSERT INTO agent_runs (run_id, started_ts, finished_ts, kill_switch, note)
            VALUES (?, ?, NULL, ?, ?)
            """,
            [run_id, started_ts or _now(), kill_switch, note],
        )
        return run_id

    def finish_agent_run(
        self,
        *,
        run_id: str,
        signals_seen: int = 0,
        orders_placed: int = 0,
        fills_scraped: int = 0,
        export_ok: bool | None = None,
        error: str | None = None,
        finished_ts: datetime | None = None,
    ) -> None:
        """收尾回填本轮心跳。本库唯一 UPDATE(见模块注释):仅限 agent_runs 单行、
        仅限 finished_ts IS NULL 的未收尾行;崩溃即留 NULL 作证据。"""
        cur = self._conn.execute(
            """
            UPDATE agent_runs
            SET finished_ts = ?, signals_seen = ?, orders_placed = ?,
                fills_scraped = ?, export_ok = ?, error = ?
            WHERE run_id = ? AND finished_ts IS NULL
            """,
            [finished_ts or _now(), signals_seen, orders_placed, fills_scraped, export_ok, error, run_id],
        )
        if cur.fetchone()[0] != 1:
            raise LedgerWriteError(f"agent_run 收尾失败(不存在或已收尾): {run_id}")

    # ── ingest_watermark(§4.6)──────────────────────────────────────────

    def insert_watermark(
        self,
        *,
        last_seen_call_ts: datetime,
        calls_seen: int = 0,
        poll_ts: datetime | None = None,
        note: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO ingest_watermark (poll_ts, last_seen_call_ts, calls_seen, note) VALUES (?, ?, ?, ?)",
            [poll_ts or _now(), last_seen_call_ts, calls_seen, note],
        )

    def current_watermark(self) -> datetime | None:
        return self._conn.execute("SELECT max(last_seen_call_ts) FROM ingest_watermark").fetchone()[0]
