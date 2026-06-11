"""PaperBroker — 本地模拟盘撮合(P2,取代 Firstrade 接通)。

无浏览器、无真金、无券商:消费规则引擎 followed 决策 → 按**真实市场价**(信号时点
日收盘,Data 价源)模拟成交 + 可配滑点 → 复用现有 ledger writer 写 orders/fills/
positions_daily(演练已验全链,这次 fill 用真价)→ 退出引擎按真实日收盘前向评估
(21d 到期 / 止损 / 翻空)。

价源走 Protocol(可注入假价测试、不锁死实现)。默认适配器读 stock-picker prices.db
的 price_cache(ticker,date,close)——**只读**(红线:stock-picker 侧绝不写)。
日级 close 是唯一可得粒度(prices.db 无 OHLCV),撮合价口径=信号入场交易日收盘,
与 S 账 T+1 close 口径同源,可比。

诚实边界:
- 成交价=日收盘,非盘中真实成交价(无 tick 数据);滑点是建模假设不是实测。
- 无 paper trading 真实账户,这是**本地账本模拟**;E 账=本地成交,绝不等同券商真账。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from loguru import logger

from src.execution.ledger import LedgerWriter
from src.rules.engine import entry_window

_CENT = Decimal("0.01")
_ET = ZoneInfo("America/New_York")


def _close_ts(d: date) -> datetime:
    """成交时点 = 该交易日 16:00 ET(收盘);TIMESTAMPTZ 读回不会跨日(午夜 UTC 会退前一天)。"""
    return datetime.combine(d, time(16, 0), tzinfo=_ET)


def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS")


class PriceSource(Protocol):
    """信号时点价源:给 ticker + 交易日 → 当日收盘。无数据返回 None(不假装有)。"""

    def close_on(self, ticker: str, d: date) -> Decimal | None: ...


class PricesDbClose:
    """stock-picker prices.db 的 price_cache 只读适配器(close-only,日级)。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def close_on(self, ticker: str, d: date) -> Decimal | None:
        # read_only=True:绝不写 stock-picker 侧(红线)
        con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT close FROM price_cache WHERE ticker = ? AND date = ?",
                (ticker, d.isoformat()),
            ).fetchone()
        finally:
            con.close()
        if row is None or row[0] is None:
            return None
        return Decimal(str(row[0])).quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PaperBrokerConfig:
    """撮合参数。任何改动按红线 3 升 rule_version(严禁静默改参)。"""

    per_order_usd: float = 5_000.0
    slippage_bps: float = 0.0  # 单边滑点(基点);起步 0,买价上浮/卖价下压
    hold_days: int = 21  # 持有满 N 个交易日到期退出(对标 call_outcomes 21d)
    stop_loss_pct: float | None = None  # 收盘跌破 entry*(1-pct) 止损;None=不启用(不臆造阈值)


@dataclass(frozen=True)
class OpenLot:
    signal_id: str
    order_id: str
    handle: str
    ticker: str
    qty: Decimal
    entry_date: date
    entry_price: Decimal


class PaperBroker:
    """本地模拟盘:单写者复用 LedgerWriter;价源可注入。无 kill/PAPER_ONLY 拦截
    (本地模拟本就无真金,但仍走 ledger append-only 留档纪律)。"""

    def __init__(
        self,
        writer: LedgerWriter,
        price_source: PriceSource,
        config: PaperBrokerConfig | None = None,
    ) -> None:
        self.w = writer
        self.px = price_source
        self.cfg = config or PaperBrokerConfig()

    # ── 价格/滑点 ───────────────────────────────────────────────────────

    def _fill_price(self, raw: Decimal, side: str) -> Decimal:
        """滑点:买上浮、卖下压 slippage_bps 个基点。"""
        adj = Decimal(str(self.cfg.slippage_bps)) / Decimal("10000")
        factor = (Decimal(1) + adj) if side == "buy" else (Decimal(1) - adj)
        return (raw * factor).quantize(_CENT, rounding=ROUND_HALF_UP)

    @staticmethod
    def _sessions_between(start: date, end: date) -> int:
        """(start, end] 内的 NYSE 交易日数(start 不计,end 计)。"""
        if end <= start:
            return 0
        sched = _xnys().sessions_in_range(start.isoformat(), end.isoformat())
        return sum(1 for s in sched if s.date() > start)

    # ── 入场 ─────────────────────────────────────────────────────────────

    def enter(
        self,
        *,
        signal_id: str,
        handle: str,
        ticker: str,
        call_ts: datetime,
        rule_version: str,
        entry_date: date | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """对一条 followed 决策按入场交易日收盘建仓。

        入场日 = 规则引擎 entry_window(call_ts) 的 T_entry(call 日后首个交易日),
        撮合价 = 该日收盘 + 买方滑点。无价(停牌/缺数据)则不开仓返回 None(诚实,不假装)。
        """
        t_entry = entry_date or entry_window(call_ts)[0]
        raw = self.px.close_on(ticker, t_entry)
        if raw is None:
            logger.info("paper enter 跳过 {}({}):入场日 {} 无收盘价", signal_id, ticker, t_entry)
            return None
        fill_price = self._fill_price(raw, "buy")
        qty = Decimal(int(Decimal(str(self.cfg.per_order_usd)) / fill_price))
        if qty < 1:
            logger.info("paper enter 跳过 {}:单价 {} 超单仓预算,整股=0", signal_id, fill_price)
            return None
        submitted = now or _close_ts(t_entry)
        oid = self.w.open_order(
            signal_id=signal_id,
            ticker=ticker,
            side="buy",
            qty=qty,
            order_type="market",
            submitted_ts=submitted,
            call_to_submit_ms=int((submitted - call_ts).total_seconds() * 1000),
            rule_version=rule_version,
            note=f"paper enter @ {t_entry} close(raw={raw}, slip={self.cfg.slippage_bps}bps)",
        )
        self.w.append_order_event(order_id=oid, status="filled", broker_order_ref=f"paper-{oid[-8:]}")
        self.w.insert_fill(
            order_id=oid,
            fill_ts=submitted,
            qty=qty,
            price=fill_price,
            raw_text=f"PAPER FILL buy {qty} {ticker} @ {fill_price} ({t_entry} close {raw})",
        )
        logger.info("paper enter {} {} qty={} @ {}", signal_id, ticker, qty, fill_price)
        return oid

    # ── 持仓重建(从 ledger,单一真相)──────────────────────────────────

    def open_lots(self) -> list[OpenLot]:
        """当前未平仓 = 有成交的 buy 单且其 signal 还没有 sell 单(v1 一信号一仓)。"""
        rows = self.w.conn.execute(
            """
            SELECT b.signal_id, b.order_id, s.handle, b.ticker,
                   f.filled_qty, f.first_fill_ts, f.avg_fill_price
            FROM v_orders_current b
            JOIN signals s USING (signal_id)
            JOIN v_order_filled f USING (order_id)
            WHERE b.side = 'buy'
              AND NOT EXISTS (
                  SELECT 1 FROM v_orders_current x
                  WHERE x.signal_id = b.signal_id AND x.side = 'sell')
            """
        ).fetchall()
        return [
            OpenLot(
                signal_id=r[0],
                order_id=r[1],
                handle=r[2],
                ticker=r[3],
                qty=r[4],
                entry_date=r[5].date(),
                entry_price=Decimal(str(r[6])).quantize(_CENT),
            )
            for r in rows
        ]

    # ── 退出引擎(前向,按真实日收盘评估)──────────────────────────────

    def _exit_reason(self, lot: OpenLot, as_of: date, close: Decimal, flips: dict[tuple[str, str], date]) -> str | None:
        """优先级:翻空 > 止损 > 到期(任一触发即平)。"""
        flip_date = flips.get((lot.handle, lot.ticker))
        if flip_date is not None and flip_date <= as_of:
            return "direction_flip"
        if self.cfg.stop_loss_pct is not None:
            floor = (lot.entry_price * (Decimal(1) - Decimal(str(self.cfg.stop_loss_pct)))).quantize(_CENT)
            if close <= floor:
                return "stop_loss"
        if self._sessions_between(lot.entry_date, as_of) >= self.cfg.hold_days:
            return "hold_21d"
        return None

    def forward_day(
        self,
        as_of: date,
        *,
        rule_version: str,
        flips: dict[tuple[str, str], date] | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        """对所有未平仓单评估退出;触发的按 as_of 当日收盘卖出。返回平仓的 signal_id 列表。"""
        flips = flips or {}
        exited: list[str] = []
        for lot in self.open_lots():
            close = self.px.close_on(lot.ticker, as_of)
            if close is None:
                continue  # 当日无价:不强平,留到有价的下一交易日(诚实)
            reason = self._exit_reason(lot, as_of, close, flips)
            if reason is None:
                continue
            fill_price = self._fill_price(close, "sell")
            ts = now or _close_ts(as_of)
            sid = self.w.open_order(
                signal_id=lot.signal_id,
                ticker=lot.ticker,
                side="sell",
                qty=lot.qty,
                order_type="market",
                submitted_ts=ts,
                rule_version=rule_version,
                exit_reason=reason,
                note=f"paper exit {reason} @ {as_of} close {close}",
            )
            self.w.append_order_event(order_id=sid, status="filled", broker_order_ref=f"paper-{sid[-8:]}")
            self.w.insert_fill(
                order_id=sid,
                fill_ts=ts,
                qty=lot.qty,
                price=fill_price,
                raw_text=f"PAPER FILL sell {lot.qty} {lot.ticker} @ {fill_price} ({reason}, {as_of} close {close})",
            )
            logger.info("paper exit {} {} {} qty={} @ {}", lot.signal_id, lot.ticker, reason, lot.qty, fill_price)
            exited.append(lot.signal_id)
        return exited

    # ── 持仓快照(对账锚点)────────────────────────────────────────────

    def snapshot_positions(self, as_of: date) -> int:
        """对未平仓单按 as_of 收盘写 positions_daily(对账锚点)。返回快照票数。"""
        n = 0
        for lot in self.open_lots():
            close = self.px.close_on(lot.ticker, as_of)
            unreal = ((close - lot.entry_price) * lot.qty).quantize(_CENT) if close is not None else None
            self.w.insert_position_snapshot(
                snapshot_date=as_of,
                ticker=lot.ticker,
                qty=lot.qty,
                avg_cost=lot.entry_price,
                close=close,
                unrealized_pnl=unreal,
                raw_text=f"PAPER position {lot.ticker} {lot.qty} @cost {lot.entry_price} close {close}",
            )
            n += 1
        return n
