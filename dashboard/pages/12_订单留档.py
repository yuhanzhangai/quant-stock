"""订单留档页 — 每单点开回答审计五问,含原帖全文快照。

r3 口径:同一原帖可对应多条 signal(多 ticker),页面按 signal 列,帖子快照复用。
tweet_blocked=TRUE 的原帖正文禁止对外展示(spec §9 Dash 会签项)。
数据源:优先 ledger_reader(parquet 导出),无导出时回落 ledger_mock(§2)。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ledger_mock
import ledger_reader
import pandas as pd
import streamlit as st

lm = ledger_reader if ledger_reader.export_available() else ledger_mock

st.set_page_config(page_title="订单留档", page_icon="📜", layout="wide")
st.title("📜 跟单订单留档(审计五问)")

if lm.IS_MOCK:
    st.warning("**🚧 MOCK 数据预览 —— ledger 实施(P1)完成后接真数据**(schema 对齐 spec r3)", icon="🚧")
    data_asof = lm.EXPORT_TS
else:
    _status, _age_min, data_asof = ledger_reader.freshness()
    if _status == "stale":
        st.warning(f"**数据陈旧**:最近导出 {data_asof:%m-%d %H:%M UTC}({_age_min:.0f} 分钟前)", icon="⏳")
st.caption(f"数据导出于 {data_asof:%Y-%m-%d %H:%M UTC} · append-only 审计件,只读展示")

signals = lm.load_signals()
orders = lm.load_orders_current()
filled = lm.load_order_filled()
fills = lm.load_fills_effective()


def render_tweet_snapshot(sig_row, title: str = "原帖全文快照") -> None:
    """原帖快照展示;blocked 一律不出正文(数据层快照仍留档,仅展示禁用)。"""
    if bool(sig_row["tweet_blocked"]):
        st.error(
            "🚫 该原帖已被合规屏蔽(tweet_blocked=TRUE),正文不对外展示。留档库内仍保有快照,审计请走 operator 通道。"
        )
        return
    st.markdown(
        f"**{title}** — @{sig_row['handle']} · "
        f"{sig_row['call_ts']:%Y-%m-%d %H:%M UTC} · "
        f"[原帖链接]({sig_row['tweet_url']})"
    )
    st.info(sig_row["tweet_text"])


# ── 信号列表(r2:按 signal 列,同帖多 ticker 多行)─────────────────
st.subheader("信号收录(按 signal 列)")
c1, c2, c3 = st.columns(3)
f_handle = c1.multiselect("博主", sorted(signals["handle"].unique()))
f_ticker = c2.multiselect("标的", sorted(signals["ticker"].unique()))
f_decision = c3.multiselect("决定", ["followed", "skipped"])

sig_view = signals.copy()
if f_handle:
    sig_view = sig_view[sig_view["handle"].isin(f_handle)]
if f_ticker:
    sig_view = sig_view[sig_view["ticker"].isin(f_ticker)]
if f_decision:
    sig_view = sig_view[sig_view["decision"].isin(f_decision)]

n_orders = orders.groupby("signal_id").size().rename("订单数")
sig_table = sig_view.merge(n_orders, on="signal_id", how="left").fillna({"订单数": 0})
st.dataframe(
    sig_table[
        [
            "signal_id",
            "tweet_id",
            "handle",
            "ticker",
            "direction",
            "tier",
            "call_ts",
            "decision",
            "decision_reason",
            "订单数",
        ]
    ].sort_values("call_ts", ascending=False),
    use_container_width=True,
    hide_index=True,
)
st.caption("同一 tweet_id 多行 = 同帖喊多只票(r2),帖子快照复用,跟/不跟独立判定。")

# ── 订单详情:审计五问 ─────────────────────────────────────────────
st.subheader("订单详情(点开看审计五问)")
order_ids = orders["order_id"].tolist()
_labels = {r["order_id"]: f"{r['order_id']} — {r['ticker']} {r['side']} {r['status']}" for _, r in orders.iterrows()}
sel = st.selectbox("选择订单", order_ids, format_func=lambda oid: _labels.get(oid, oid))

o = orders[orders["order_id"] == sel].iloc[0]
s = signals[signals["signal_id"] == o["signal_id"]].iloc[0]
agg = filled[filled["order_id"] == sel]
o_fills = fills[fills["order_id"] == sel]

st.markdown("---")
q1, q2 = st.columns(2)
with q1:
    st.markdown("#### ① 何时")
    delay_min = o["call_to_submit_ms"] / 60_000 if pd.notna(o["call_to_submit_ms"]) else 0.0
    st.markdown(
        f"- 博主发帖:`{s['call_ts']:%Y-%m-%d %H:%M:%S UTC}`\n"
        f"- 我们收录:`{s['ingested_ts']:%Y-%m-%d %H:%M:%S UTC}`\n"
        f"- 提交下单:`{o['submitted_ts']:%Y-%m-%d %H:%M:%S UTC}`"
        f"(发帖→下单 **{delay_min:.0f} 分钟**)"
    )

    st.markdown("#### ③ 依据什么规则")
    st.markdown(
        f"- 诚实榜 tier:**{s['tier']}**(CSV {s['tier_csv_date']})\n"
        f"- 决定:**{s['decision']}** / `{s['decision_reason']}`\n"
        f"- 规则版本:`{o['rule_version']}`\n"
        f"- conviction/confidence:{s['conviction']} / {s['confidence']}"
    )

    st.markdown("#### ④ 实际成交多少")
    if not agg.empty:
        a = agg.iloc[0]
        st.markdown(f"- 成交 **{a['filled_qty']:g} 股** @ 均价 **${a['avg_fill_price']:.2f}**({int(a['n_fills'])} 笔)")
    else:
        st.markdown("- 暂无成交")

    st.markdown("#### ⑤ 现在状态")
    st.markdown(
        f"- 状态:**{o['status']}**"
        + (f" · 退出原因:`{o['exit_reason']}`" if pd.notna(o["exit_reason"]) else "")
        + (" · ⚠️ kill-switch 单" if o["kill_switch_engaged"] else "")
        + (f" · 券商单号 `{o['broker_order_ref']}`" if pd.notna(o["broker_order_ref"]) else "")
    )

with q2:
    st.markdown("#### ② 跟谁的哪条帖")
    render_tweet_snapshot(s)
    if pd.notna(o["exit_trigger_signal_id"]):
        trig = signals[signals["signal_id"] == o["exit_trigger_signal_id"]].iloc[0]
        render_tweet_snapshot(trig, title="平仓依据:反向喊单快照")

with st.expander("逐笔成交明细(含 Firstrade 页面回采原文)"):
    if o_fills.empty:
        st.write("无成交记录")
    else:
        st.dataframe(
            o_fills[["fill_id", "fill_ts", "qty", "price", "raw_text", "scraped_ts"]],
            use_container_width=True,
            hide_index=True,
        )

with st.expander("订单事件流(状态机轨迹,append-only;corrects_seq 非空 = 终态后更正行,r3)"):
    ev = lm.load_order_events(sel)
    st.dataframe(
        ev[["seq", "event_ts", "status", "corrects_seq", "kill_switch_engaged", "note"]],
        use_container_width=True,
        hide_index=True,
    )

# ── 同信号链路:入场 ↔ 平仓 + 单笔已实现盈亏 ───────────────────────
st.markdown("---")
st.subheader("本次跟单链路(同 signal 全部订单)")
chain = (
    orders[orders["signal_id"] == o["signal_id"]].merge(filled, on="order_id", how="left").sort_values("submitted_ts")
)
st.dataframe(
    chain[["order_id", "side", "qty", "status", "submitted_ts", "exit_reason", "filled_qty", "avg_fill_price"]],
    use_container_width=True,
    hide_index=True,
)

entries = chain[(chain["side"] == "buy") & (chain["status"] == "filled")]
exits = chain[(chain["side"] == "sell") & (chain["status"] == "filled")]
if not entries.empty and not exits.empty:
    buy_amt = (entries["filled_qty"] * entries["avg_fill_price"]).sum()
    sell_amt = (exits["filled_qty"] * exits["avg_fill_price"]).sum()
    closed_qty = min(entries["filled_qty"].sum(), exits["filled_qty"].sum())
    pnl = sell_amt - buy_amt * (closed_qty / entries["filled_qty"].sum())
    st.metric("本次跟单已实现盈亏(模拟盘,零佣金口径)", f"${pnl:+,.2f}")
elif not entries.empty:
    st.caption("持仓中,尚无已实现盈亏。")
