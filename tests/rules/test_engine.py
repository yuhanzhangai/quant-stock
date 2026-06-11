"""引擎纯函数测试:门序/窗口/issuer 归并/择优/B4,全部合成输入,无 IO。

日历事实(NYSE 2026-06):06-03(三)~06-05(五)、06-08(一)~06-12(五)为交易日;
06-06/07 周末。决策时点统一 2026-06-09 15:30 ET(= 19:30 UTC)。
"""

from datetime import UTC, datetime

import polars as pl
import pytest

from src.rules.engine import (
    PdtState,
    PortfolioState,
    Position,
    RuleParams,
    decide,
    entry_window,
    issuer_key,
)

DECISION_TS = datetime(2026, 6, 9, 19, 30, tzinfo=UTC)  # 周二 15:30 ET
MON = datetime(2026, 6, 8, 14, 0, tzinfo=UTC)           # 周一盘中喊单 → T_entry=06-09

_SCHEMA = pl.Schema({
    "signal_id": pl.String, "handle": pl.String, "ticker": pl.String, "direction": pl.String,
    "call_ts": pl.Datetime("us", "UTC"), "conviction": pl.String, "confidence": pl.Float64,
})


def cands(*rows: dict) -> pl.DataFrame:
    base = {"handle": "alice", "ticker": "AAPL", "direction": "bullish",
            "call_ts": MON, "conviction": "high", "confidence": 0.9}
    return pl.DataFrame([{**base, **r} for r in rows], schema=_SCHEMA)


def run(df: pl.DataFrame, **kw) -> dict[str, tuple[str, str]]:
    defaults: dict = {"decision_ts": DECISION_TS, "pdt": PdtState(0, 100_000.0),
                      "prices": {"AAPL": 50.0, "GOOG": 200.0, "GOOGL": 201.0, "TSLA": 300.0}}
    out = decide(df, **{**defaults, **kw})
    return {r["signal_id"]: (r["decision"], r["decision_reason"]) for r in out.to_dicts()}


def test_happy_path_followed():
    got = run(cands({"signal_id": "s1"}))
    assert got == {"s1": ("followed", "all_gates_passed")}


def test_entry_window_utc_date_to_next_session():
    t_entry, t_retry = entry_window(MON)
    assert (t_entry.isoformat(), t_retry.isoformat()) == ("2026-06-09", "2026-06-10")
    # 周六喊单(UTC)→ T_entry 周一,顺延不丢单(spec §0)
    t_entry, t_retry = entry_window(datetime(2026, 6, 6, 18, 0, tzinfo=UTC))
    assert (t_entry.isoformat(), t_retry.isoformat()) == ("2026-06-08", "2026-06-09")


def test_entry_window_rejects_naive_ts():
    with pytest.raises(ValueError, match="时区"):
        entry_window(datetime(2026, 6, 8, 14, 0))


def test_pending_before_t_entry_emits_no_row():
    # 决策日(06-09)当天的喊单 → T_entry=06-10,窗口未到:不出行,留待下轮
    got = run(cands({"signal_id": "s1", "call_ts": datetime(2026, 6, 9, 13, 0, tzinfo=UTC)}))
    assert got == {}


def test_stale_after_retry_day():
    got = run(cands({"signal_id": "s1", "call_ts": datetime(2026, 6, 3, 14, 0, tzinfo=UTC)}))
    assert got == {"s1": ("skipped", "signal_stale")}


def test_weekend_call_valid_on_retry_day():
    got = run(cands({"signal_id": "s1", "call_ts": datetime(2026, 6, 6, 18, 0, tzinfo=UTC)}))
    assert got == {"s1": ("followed", "all_gates_passed")}


def test_bearish_candidate_is_exit_trigger():
    got = run(cands({"signal_id": "s1", "direction": "bearish"}))
    assert got == {"s1": ("skipped", "exit_trigger")}


def test_kill_switch_skips_in_window_but_not_pending():
    df = cands({"signal_id": "in_win"},
               {"signal_id": "pend", "call_ts": datetime(2026, 6, 9, 13, 0, tzinfo=UTC)})
    got = run(df, kill_switch=True)
    assert got == {"in_win": ("skipped", "kill_switch_on")}  # pending 不被瞬态定罪


def test_manual_block():
    df = cands({"signal_id": "s1"}, {"signal_id": "s2", "handle": "bob", "ticker": "TSLA"})
    got = run(df, blocked_handles=frozenset({"alice"}), blocked_tickers=frozenset({"TSLA"}))
    assert got["s1"] == ("skipped", "manual_block")
    assert got["s2"] == ("skipped", "manual_block")


def test_issuer_key_mapping():
    assert issuer_key("GOOG") == issuer_key("GOOGL") == "GOOG"
    assert issuer_key("AAPL") == "AAPL"


def test_dedup_same_handle_issuer_keeps_latest():
    # GOOG 与 GOOGL 同发行人:同 handle 两条 bullish 视为重复,留 call_ts 较新者(v0.1 §7)
    df = cands({"signal_id": "old", "ticker": "GOOG", "call_ts": MON},
               {"signal_id": "new", "ticker": "GOOGL",
                "call_ts": datetime(2026, 6, 8, 15, 0, tzinfo=UTC)})
    got = run(df)
    assert got["old"] == ("skipped", "duplicate_signal")
    assert got["new"] == ("followed", "all_gates_passed")


def test_direction_conflict_via_sibling_class():
    # alice 看空 GOOGL → bob 的 GOOG bullish 同发行人冲突(spec §1.3 + §7)
    bearish = pl.DataFrame(
        [{"handle": "alice", "ticker": "GOOGL", "call_ts": datetime(2026, 6, 7, 12, 0, tzinfo=UTC)}],
        schema=pl.Schema({"handle": pl.String, "ticker": pl.String, "call_ts": pl.Datetime("us", "UTC")}))
    got = run(cands({"signal_id": "s1", "handle": "bob", "ticker": "GOOG"}), recent_bearish=bearish)
    assert got == {"s1": ("skipped", "direction_conflict")}


def test_bearish_outside_7d_window_no_conflict():
    bearish = pl.DataFrame(
        [{"handle": "alice", "ticker": "GOOG", "call_ts": datetime(2026, 6, 1, 12, 0, tzinfo=UTC)}],
        schema=pl.Schema({"handle": pl.String, "ticker": pl.String, "call_ts": pl.Datetime("us", "UTC")}))
    got = run(cands({"signal_id": "s1", "ticker": "GOOG"}), recent_bearish=bearish)
    assert got == {"s1": ("followed", "all_gates_passed")}


def test_tradability_gates():
    df = cands({"signal_id": "no_price", "ticker": "ZZZZ"},
               {"signal_id": "penny", "ticker": "PNY"},
               {"signal_id": "zero_share", "ticker": "BIG"},
               {"signal_id": "granular", "ticker": "GRN"})
    got = run(df, prices={"PNY": 2.99, "BIG": 6000.0, "GRN": 2600.0})
    assert got["no_price"] == ("skipped", "ticker_not_tradable")
    assert got["penny"] == ("skipped", "ticker_not_tradable")
    assert got["zero_share"] == ("skipped", "risk_cap_exceeded")   # ⌊5000/6000⌋=0
    assert got["granular"] == ("skipped", "risk_cap_exceeded")     # 2600 < 80%×5000


def test_merge_same_issuer_higher_wilson_wins():
    df = cands({"signal_id": "lo", "handle": "alice", "ticker": "GOOG"},
               {"signal_id": "hi", "handle": "bob", "ticker": "GOOGL"})
    got = run(df, handle_wilson={"alice": 0.51, "bob": 0.60})
    assert got["hi"] == ("followed", "all_gates_passed")
    assert got["lo"] == ("skipped", "merge_lost")


def test_position_already_open_issuer_level():
    pf = PortfolioState(open_positions=(Position("GOOGL", "carol"),))
    got = run(cands({"signal_id": "s1", "ticker": "GOOG"}), portfolio=pf)
    assert got == {"s1": ("skipped", "position_already_open")}


def test_handle_cap():
    pf = PortfolioState(open_positions=tuple(Position(t, "alice") for t in ("A", "B", "C", "D", "E")))
    df = cands({"signal_id": "capped"}, {"signal_id": "ok", "handle": "bob", "ticker": "TSLA"})
    got = run(df, portfolio=pf)
    assert got["capped"] == ("skipped", "handle_cap_exceeded")
    assert got["ok"] == ("followed", "all_gates_passed")


def test_total_slots_risk_cap():
    pf = PortfolioState(open_positions=tuple(Position(f"T{i}", f"h{i % 3}") for i in range(10)))
    got = run(cands({"signal_id": "s1"}), portfolio=pf)
    assert got == {"s1": ("skipped", "risk_cap_exceeded")}


def test_pdt_limit_blocks_new_entries():
    got = run(cands({"signal_id": "s1"}), pdt=PdtState(day_trades_5d=3, settled_cash=100_000.0))
    assert got == {"s1": ("skipped", "pdt_limit_reached")}


def test_settled_cash_sequential_depletion():
    # 现金只够一单:优先级高者(wilson)先得,后者 insufficient_settled_cash(B4 顺序扣减)
    df = cands({"signal_id": "first", "handle": "bob", "ticker": "TSLA"},
               {"signal_id": "second", "handle": "alice", "ticker": "AAPL"})
    got = run(df, pdt=PdtState(0, 6_000.0), handle_wilson={"bob": 0.60, "alice": 0.51})
    assert got["first"] == ("followed", "all_gates_passed")
    assert got["second"] == ("skipped", "insufficient_settled_cash")


def test_priority_conviction_tiebreak_on_equal_wilson():
    df = cands({"signal_id": "hi_conv", "handle": "alice", "ticker": "GOOG", "conviction": "high"},
               {"signal_id": "lo_conv", "handle": "bob", "ticker": "GOOGL", "conviction": "low"})
    got = run(df, handle_wilson={"alice": 0.55, "bob": 0.55})
    assert got["hi_conv"] == ("followed", "all_gates_passed")
    assert got["lo_conv"] == ("skipped", "merge_lost")


def test_rule_version_stamped_on_every_row():
    df = cands({"signal_id": "s1"}, {"signal_id": "s2", "direction": "bearish"})
    out = decide(df, decision_ts=DECISION_TS, pdt=PdtState(0, 100_000.0), prices={"AAPL": 50.0},
                 params=RuleParams())
    assert set(out["rule_version"].to_list()) == {"v0.1"}
