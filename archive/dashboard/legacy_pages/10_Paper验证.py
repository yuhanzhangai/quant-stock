"""Paper vs Backtest 对比页面 — 从 DB 索引。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="Paper验证", page_icon="📝", layout="wide")
st.title("📝 Paper Trading 验证")

DB_PATH = Path("data/meta/research.duckdb")

if not DB_PATH.exists():
    st.warning("研究数据库未初始化")
    st.stop()

import duckdb

conn = duckdb.connect(str(DB_PATH), read_only=True)

# Paper sessions from DB
st.subheader("Paper Trading 会话")
sessions = conn.execute(
    "SELECT session_id, strategy_name, strategy_version, initial_equity, "
    "final_equity, net_pnl, total_signals, accepted_trades, rejected_signals, "
    "status, notes, created_at "
    "FROM paper_sessions ORDER BY created_at DESC"
).fetchdf()

if len(sessions) > 0:
    st.dataframe(sessions, use_container_width=True, hide_index=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("总会话数", len(sessions))
    with col2:
        total_pnl = sessions["net_pnl"].sum() if "net_pnl" in sessions.columns else 0
        st.metric("总盈亏", f"${total_pnl:.2f}")
    with col3:
        total_trades = sessions["accepted_trades"].sum() if "accepted_trades" in sessions.columns else 0
        st.metric("总交易数", str(total_trades))
else:
    st.info("暂无 Paper Trading 记录")

# Comparison: select session + backtest from DB
st.subheader("Paper vs Backtest 对比")

backtests = conn.execute(
    "SELECT run_id, strategy_name, symbol, sharpe, trade_count, output_dir "
    "FROM backtest_runs WHERE run_type IN ('single', 'grid_best', 'baseline') "
    "ORDER BY created_at DESC LIMIT 50"
).fetchdf()

if len(sessions) > 0 and len(backtests) > 0:
    col1, col2 = st.columns(2)
    with col1:
        sel_session = st.selectbox("Paper Session", sessions["session_id"].tolist())
    with col2:
        sel_bt = st.selectbox("Backtest Run", backtests["run_id"].tolist())

    if st.button("对比"):
        # Get paper session data from DB
        paper_row = sessions[sessions["session_id"] == sel_session].iloc[0]

        # Get backtest data from DB
        bt_row = backtests[backtests["run_id"] == sel_bt].iloc[0]

        comparison = {
            "指标": ["交易数", "净盈亏/净收益", "信号数", "被拒信号", "状态"],
            "Paper": [
                str(paper_row.get("accepted_trades", 0)),
                f"${paper_row.get('net_pnl', 0):.2f}",
                str(paper_row.get("total_signals", 0)),
                str(paper_row.get("rejected_signals", 0)),
                str(paper_row.get("status", "")),
            ],
            "Backtest": [
                str(bt_row.get("trade_count", 0)),
                f"sharpe={bt_row.get('sharpe', 0):.4f}",
                "-",
                "-",
                str(bt_row.get("output_dir", "")),
            ],
        }
        st.table(comparison)

        # Try to load detailed artifacts if output_dir exists
        bt_output = bt_row.get("output_dir", "")
        if bt_output and Path(bt_output).exists():
            metrics_path = Path(bt_output) / "metrics.json"
            if metrics_path.exists():
                with open(metrics_path, encoding="utf-8") as f:
                    st.json(json.load(f))
        elif bt_output:
            st.warning(f"DB 有记录但 artifact 缺失: {bt_output}")
elif len(sessions) == 0:
    st.info("需要 Paper Trading 数据")
else:
    st.info("需要 Backtest 数据")

conn.close()
