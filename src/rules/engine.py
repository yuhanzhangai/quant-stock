"""跟单规则引擎 v0.1:signal_candidates 行 → followed/skipped 决策(纯函数,无 IO)。

口径与门序见 docs/COPYTRADE_RULES_SPEC_V0.md §8;原因码 = ORDER_LEDGER_SPEC §5.1 封闭集
+ v0.1 新增四码(duplicate_signal / direction_conflict / merge_lost / handle_cap_exceeded,
已提请 Lead 并入 r3)。决策是终态(append-only 语义):窗口未到(决策日 < T_entry)的候选
**不出行**(pending),留待下轮;一旦出行即不可改写。

B4 软约束(ORDER_LEDGER_SPEC §4.5):引擎只读 PdtState 快照做闸门、skip 落原因码,
不记账、不裁决 ledger;settled_cash 按本批放行顺序扣减(批内先到先得,优先级排序保证确定性)。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import polars as pl
from loguru import logger

RULE_VERSION = "v0.1"

# spec §7:双类股 issuer 归并映射(封闭集,扩充必须升 rule_version;spec 与代码同步改)
_ISSUER_GROUPS_RAW: tuple[tuple[str, ...], ...] = (
    ("GOOG", "GOOGL"), ("FOX", "FOXA"), ("NWS", "NWSA"), ("UA", "UAA"),
    ("BRK.A", "BRK.B"), ("LEN", "LEN.B"), ("LBRDA", "LBRDK"),
    ("LSXMA", "LSXMB", "LSXMK"), ("CWEN", "CWEN.A"), ("HEI", "HEI.A"),
)
ISSUER_GROUPS: dict[str, str] = {t: min(g) for g in _ISSUER_GROUPS_RAW for t in g}

_ET = ZoneInfo("America/New_York")
_CONVICTION_RANK = {"high": 3, "medium": 2, "low": 1}

# decide() 实际消费的候选列(signal_candidates 的子集;缺列直接 KeyError fail loud)
REQUIRED_COLS = ("signal_id", "handle", "ticker", "direction", "call_ts", "conviction", "confidence")

OUT_SCHEMA = pl.Schema({
    "signal_id": pl.String, "decision": pl.String,
    "decision_reason": pl.String, "rule_version": pl.String,
})


@dataclass(frozen=True)
class RuleParams:
    """spec §2/§8 参数;任何改动必须升 rule_version(红线 3,严禁静默改参)。"""

    rule_version: str = RULE_VERSION
    max_positions: int = 10          # 总槽 N
    handle_cap: int = 5              # 单 handle 并发上限(Lead 批准,2-4 周复议)
    per_order_usd: float = 5_000.0   # 每单目标金额($100k 模拟盘假设的 5%,待 Exec 确认)
    min_price: float = 3.0
    min_exposure_ratio: float = 0.8  # 实际敞口 ≥ 目标金额 80%(granularity 闸)
    pdt_day_trade_limit: int = 3     # B4:day_trades_5d ≥ 3 即停新开(留 1 次余量)


@dataclass(frozen=True)
class Position:
    """一个占槽单位:已持仓或未完结入场挂单(归因 handle 唯一,spec §1.5)。"""

    ticker: str
    handle: str


@dataclass(frozen=True)
class PortfolioState:
    open_positions: tuple[Position, ...] = ()
    pending_entries: tuple[Position, ...] = ()

    def all_units(self) -> tuple[Position, ...]:
        return self.open_positions + self.pending_entries


@dataclass(frozen=True)
class PdtState:
    """B4 簿记快照(ORDER_LEDGER_SPEC §4.5 v_pdt_latest 口径;P1 阶段由调用方提供)。"""

    day_trades_5d: int
    settled_cash: float


def issuer_key(ticker: str) -> str:
    """同发行人多类股归并键;未列入映射的 ticker 原样返回。"""
    return ISSUER_GROUPS.get(ticker, ticker)


@lru_cache(maxsize=1)
def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS")


def _next_session_after(d: date) -> date:
    """严格晚于 d 的第一个 NYSE 交易日。"""
    return _xnys().date_to_session(d + timedelta(days=1), direction="next").date()


def entry_window(call_ts: datetime) -> tuple[date, date]:
    """(T_entry, T_entry+1):spec §0,基准入场日 = call_date(UTC 日期口径)后首个交易日。"""
    if call_ts.tzinfo is None:
        raise ValueError(f"call_ts 必须带时区(审计取证纪律): {call_ts!r}")
    t_entry = _next_session_after(call_ts.astimezone(UTC).date())
    return t_entry, _next_session_after(t_entry)


def _priority_key(row: dict, handle_wilson: Mapping[str, float]) -> tuple:
    """spec §1.5 择优序:wilson_lo 降序 → conviction → confidence 降序 → handle → ticker。"""
    return (
        -handle_wilson.get(row["handle"], 0.0),
        -_CONVICTION_RANK.get(row["conviction"] or "", 0),
        -(row["confidence"] if row["confidence"] is not None else 0.0),
        row["handle"],
        row["ticker"],
    )


def decide(
    candidates: pl.DataFrame,
    *,
    decision_ts: datetime,
    pdt: PdtState,
    prices: Mapping[str, float],
    portfolio: PortfolioState | None = None,
    handle_wilson: Mapping[str, float] | None = None,
    recent_bearish: pl.DataFrame | None = None,
    kill_switch: bool = False,
    blocked_handles: frozenset[str] = frozenset(),
    blocked_tickers: frozenset[str] = frozenset(),
    params: RuleParams | None = None,
) -> pl.DataFrame:
    """对候选批出决策行(signal_id + decision/decision_reason/rule_version)。

    - 窗口未到(决策日 ET < T_entry)的候选不出行(pending),由调用方下轮重入。
    - recent_bearish:近 7d PROVEN bearish 喊单(handle/ticker/call_ts,调用方直查 analyst_calls);
      None 视同空集——冲突门照跑但没有证据输入,调用方必须保证数据面(runner 负责)。
    """
    if portfolio is None:
        portfolio = PortfolioState()
    if params is None:
        params = RuleParams()
    if handle_wilson is None:
        handle_wilson = {}
    out: list[tuple[str, str, str, str]] = []

    def emit(sid: str, decision: str, reason: str) -> None:
        out.append((sid, decision, reason, params.rule_version))

    decision_date = decision_ts.astimezone(_ET).date()

    # 冲突证据集:7d 窗口内 PROVEN bearish 的 issuer(spec §1.3 / §7,issuer 级)
    flip_issuers: set[str] = set()
    if recent_bearish is not None and recent_bearish.height:
        lo = decision_ts - timedelta(days=7)
        for b in recent_bearish.to_dicts():
            if lo <= b["call_ts"] <= decision_ts:
                flip_issuers.add(issuer_key(b["ticker"]))

    # 门 1-3.5:逐候选终态判定;窗口未到 → pending 不出行
    survivors: list[dict] = []
    pending = 0
    for r in candidates.to_dicts():
        sid = r["signal_id"]
        if r["direction"] != "bullish":
            emit(sid, "skipped", "exit_trigger")
            continue
        t_entry, t_retry = entry_window(r["call_ts"])
        if decision_date < t_entry:
            pending += 1
            continue
        if decision_date > t_retry:
            emit(sid, "skipped", "signal_stale")
            continue
        if kill_switch:
            emit(sid, "skipped", "kill_switch_on")
            continue
        if r["handle"] in blocked_handles or r["ticker"] in blocked_tickers:
            emit(sid, "skipped", "manual_block")
            continue
        survivors.append(r)

    # 门 4:批内去重(同 handle×issuer×direction 留 call_ts 最新,平手取 signal_id 大者保确定性)
    best: dict[tuple[str, str, str], dict] = {}
    for r in survivors:
        k = (r["handle"], issuer_key(r["ticker"]), r["direction"])
        cur = best.get(k)
        if cur is None:
            best[k] = r
        elif (r["call_ts"], r["signal_id"]) > (cur["call_ts"], cur["signal_id"]):
            emit(cur["signal_id"], "skipped", "duplicate_signal")
            best[k] = r
        else:
            emit(r["signal_id"], "skipped", "duplicate_signal")

    # 门 5-7:冲突 / 可交易性 / 整数股约束
    sized: list[dict] = []
    for r in best.values():
        sid, ticker = r["signal_id"], r["ticker"]
        if issuer_key(ticker) in flip_issuers:
            emit(sid, "skipped", "direction_conflict")
            continue
        price = prices.get(ticker)
        if price is None or price < params.min_price:
            emit(sid, "skipped", "ticker_not_tradable")
            continue
        shares = math.floor(params.per_order_usd / price)
        exposure = shares * price
        if shares < 1 or exposure < params.min_exposure_ratio * params.per_order_usd:
            logger.debug("sizing skip {}: price={} shares={} exposure={:.2f}(zero_share/granularity)",
                         sid, price, shares, exposure)
            emit(sid, "skipped", "risk_cap_exceeded")
            continue
        sized.append({**r, "_est_cost": exposure})

    # 门 8:同发行人合并(优先级首位胜出)
    sized.sort(key=lambda r: _priority_key(r, handle_wilson))
    winners: list[dict] = []
    merged_issuers: set[str] = set()
    for r in sized:
        ik = issuer_key(r["ticker"])
        if ik in merged_issuers:
            emit(r["signal_id"], "skipped", "merge_lost")
        else:
            merged_issuers.add(ik)
            winners.append(r)

    # 门 9-13:槽位分配(顺序=优先级,确定性;B4 settled_cash 批内顺序扣减)
    held_issuers = {issuer_key(p.ticker) for p in portfolio.all_units()}
    handle_counts: Counter[str] = Counter(p.handle for p in portfolio.all_units())
    used_slots = len(portfolio.all_units())
    remaining_cash = pdt.settled_cash
    followed = 0
    for r in winners:
        sid, ik = r["signal_id"], issuer_key(r["ticker"])
        if ik in held_issuers:
            emit(sid, "skipped", "position_already_open")
        elif handle_counts[r["handle"]] >= params.handle_cap:
            emit(sid, "skipped", "handle_cap_exceeded")
        elif used_slots >= params.max_positions:
            logger.debug("slot skip {}: used={} >= N={}", sid, used_slots, params.max_positions)
            emit(sid, "skipped", "risk_cap_exceeded")
        elif pdt.day_trades_5d >= params.pdt_day_trade_limit:
            emit(sid, "skipped", "pdt_limit_reached")
        elif remaining_cash < r["_est_cost"]:
            emit(sid, "skipped", "insufficient_settled_cash")
        else:
            emit(sid, "followed", "all_gates_passed")
            held_issuers.add(ik)
            handle_counts[r["handle"]] += 1
            used_slots += 1
            remaining_cash -= r["_est_cost"]
            followed += 1

    logger.info("decide@{}: 候选 {} / pending {} / 出决策 {} / followed {}(rule_version={})",
                decision_date, candidates.height, pending, len(out), followed, params.rule_version)
    return pl.DataFrame(out, schema=OUT_SCHEMA, orient="row")
