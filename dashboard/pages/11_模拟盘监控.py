"""模拟盘监控页 — Firstrade 模拟盘:持仓/盈亏/成交/PDT/agent 健康。

数据源(spec r3 §2 读写分离):优先 ledger_reader(读 Exec 的 parquet 导出);
导出尚未上线(无 export_meta)时回落 ledger_mock(schema 同为 r3)。
meta 陈旧 = Exec 离线/HALT → 降级显示"数据陈旧",不报错。
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ledger_mock
import ledger_reader
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

lm = ledger_reader if ledger_reader.export_available() else ledger_mock

st.set_page_config(page_title="模拟盘监控", page_icon="🖥️", layout="wide")
st.title("🖥️ Firstrade 模拟盘监控")

if lm.IS_MOCK:
    st.warning("**🚧 MOCK 数据预览 —— ledger 实施(P1)完成后接真数据**(schema 对齐 spec r3)", icon="🚧")
    data_asof = lm.EXPORT_TS
else:
    _status, _age_min, data_asof = ledger_reader.freshness()
    if _status == "stale":
        st.warning(
            f"**数据陈旧**:最近导出 {data_asof:%m-%d %H:%M UTC}({_age_min:.0f} 分钟前,阈值 "
            f"{ledger_reader.STALE_AFTER.total_seconds() / 60:.0f})——Exec 可能离线或 HALT(§2 降级口径)",
            icon="⏳",
        )
st.caption(f"数据导出于 {data_asof:%Y-%m-%d %H:%M UTC}(Dash 只读 parquet 导出,不直连 ledger.duckdb)· PAPER ONLY,无真金")

# ── Agent 健康(r3 §4.5b agent_runs)──────────────────────────────────
st.subheader("Agent 健康")
runs = lm.load_agent_runs()
last = runs.iloc[0]
bad = runs[runs["error"].notna() | runs["finished_ts"].isna()]
last_ok = pd.isna(last["error"]) and pd.notna(last["finished_ts"])

c1, c2, c3, c4 = st.columns(4)
kill_on = bool(last["kill_switch"])
c1.metric("kill-switch", "🔴 已触发" if kill_on else "🟢 未触发")
c2.metric(
    "最近循环",
    f"{(last['finished_ts'] if pd.notna(last['finished_ts']) else last['started_ts']):%H:%M UTC}",
    delta="ok" if last_ok else ("崩溃未收尾" if pd.isna(last["finished_ts"]) else "error"),
    delta_color="normal" if last_ok else "inverse",
)
c3.metric(f"近 {len(runs)} 轮异常", str(len(bad)))
c4.metric("上轮导出", "✅ ok" if last["export_ok"] else "⚠️ 失败")
st.caption(
    f"上轮动作:信号 {int(last['signals_seen'])} · 下单 {int(last['orders_placed'])} · "
    f"回采 {int(last['fills_scraped'])} · 循环节奏 ~15 min(人类节奏)"
)
if kill_on:
    st.error("kill-switch 已触发:执行层停止下单,仅人工解除。", icon="🛑")
if not bad.empty:
    with st.expander(f"⚠️ 最近异常({len(bad)}:error 或崩溃未收尾)"):
        st.dataframe(bad[["run_id", "started_ts", "finished_ts", "error"]], use_container_width=True, hide_index=True)

# ── 账户与 PDT 闸门 ────────────────────────────────────────────────
st.subheader("账户与 PDT 闸门")
acct = lm.load_account_daily()
pdt = lm.load_pdt_latest().iloc[0]
today, prev = acct.iloc[-1], acct.iloc[-2]

c1, c2, c3, c4 = st.columns(4)
c1.metric("总权益", f"${today['total_equity']:,.2f}", delta=f"{today['total_equity'] - prev['total_equity']:+,.2f}")
c2.metric("累计盈亏", f"${today['total_equity'] - acct.iloc[0]['total_equity']:+,.2f}")
c3.metric("已结算资金", f"${pdt['settled_cash']:,.2f}")
pdt_ok = int(pdt["day_trades_5d"]) < 3
c4.metric(
    "5 日 day-trade",
    f"{int(pdt['day_trades_5d'])} / 3",
    delta="闸门放行" if pdt_ok else "闸门拦截",
    delta_color="normal" if pdt_ok else "inverse",
)

fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=acct["snapshot_date"], y=acct["total_equity"], mode="lines+markers", name="总权益", line={"color": "#2ca02c"}
    )
)
fig.update_layout(
    title=f"账户权益曲线(EOD{',MOCK' if lm.IS_MOCK else ''})",
    height=320,
    margin={"l": 40, "r": 20, "t": 40, "b": 30},
    yaxis_title="USD",
)
st.plotly_chart(fig, use_container_width=True)

# ── 当前持仓 + 对账 ────────────────────────────────────────────────
st.subheader("当前持仓(最新 EOD 快照)")
pos = lm.load_positions_eod()
latest_date = pos["snapshot_date"].max()
cur = pos[pos["snapshot_date"] == latest_date]
recon = lm.load_recon_status()
recon_bad = recon[recon["recon"] != "ok"]

col_l, col_r = st.columns([3, 1])
with col_l:
    st.dataframe(
        cur[["ticker", "qty", "avg_cost", "close", "unrealized_pnl"]],
        use_container_width=True,
        hide_index=True,
        column_config={"unrealized_pnl": st.column_config.NumberColumn("未实现盈亏", format="$%.2f")},
    )
    st.caption(f"快照日 {latest_date}(盘中持仓以 EOD 锚点 + 当日 fills 推算,口径见 spec §7)")
with col_r:
    if recon_bad.empty:
        st.success(f"对账 {len(recon)}/{len(recon)} OK", icon="✅")
    else:
        st.error(f"对账异常 {len(recon_bad)} 日 — 已停新单", icon="🛑")
        st.dataframe(recon_bad, hide_index=True)

# ── 成交记录 ──────────────────────────────────────────────────────
st.subheader("成交记录(有效成交,已剔除作废对)")
fills = lm.load_fills_effective()
orders = lm.load_orders_current()
fills_view = fills.merge(orders[["order_id", "ticker", "side", "exit_reason"]], on="order_id", how="left").sort_values(
    "fill_ts", ascending=False
)
st.dataframe(
    fills_view[["fill_ts", "ticker", "side", "qty", "price", "order_id", "exit_reason", "raw_text"]],
    use_container_width=True,
    hide_index=True,
)

# ── 订单现状 + 信号管线 ────────────────────────────────────────────
col_l, col_r = st.columns(2)
with col_l:
    st.subheader("订单现状")
    open_orders = orders[~orders["status"].isin(["filled", "cancelled", "rejected", "expired"])]
    st.dataframe(
        orders[["order_id", "ticker", "side", "qty", "status", "submitted_ts", "exit_reason", "kill_switch_engaged"]],
        use_container_width=True,
        hide_index=True,
    )
    st.caption(f"未终态订单:{len(open_orders)}")
with col_r:
    st.subheader("信号管线新鲜度")
    wm = lm.load_ingest_watermark_latest().iloc[0]
    now = datetime.now(UTC) if not lm.IS_MOCK else lm.EXPORT_TS
    poll_age_min = (now - wm["poll_ts"]).total_seconds() / 60
    st.metric(
        "上次轮询",
        f"{wm['poll_ts']:%H:%M UTC}",
        delta=f"{poll_age_min:.0f} 分钟前",
        delta_color="normal" if poll_age_min < 60 else "inverse",
    )
    st.metric("水位 last_seen_call_ts", f"{wm['last_seen_call_ts']:%m-%d %H:%M UTC}")
    st.metric("本轮新喊单(过滤前)", int(wm["calls_seen"]))
