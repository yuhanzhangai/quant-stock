"""Paper vs Backtest 对比页面。"""

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

# Paper sessions
st.subheader("Paper Trading 会话")
sessions = conn.execute(
    "SELECT session_id, strategy_name, strategy_version, initial_equity, "
    "final_equity, net_pnl, total_signals, accepted_trades, rejected_signals, "
    "status, created_at "
    "FROM paper_sessions ORDER BY created_at DESC"
).fetchdf()

if len(sessions) > 0:
    st.dataframe(sessions, use_container_width=True, hide_index=True)

    # Summary
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

# Comparison section
st.subheader("Paper vs Backtest 对比")

# Check for paper session files
session_dir = Path("data/research/paper_sessions")
backtest_dir = Path("data/research/backtests")

paper_sessions = sorted(session_dir.glob("session_id=*")) if session_dir.exists() else []
backtest_runs = sorted(backtest_dir.glob("run_id=*")) if backtest_dir.exists() else []

if paper_sessions and backtest_runs:
    col1, col2 = st.columns(2)
    with col1:
        selected_paper = st.selectbox("Paper Session", [p.name for p in paper_sessions])
    with col2:
        selected_bt = st.selectbox("Backtest Run", [b.name for b in backtest_runs])

    if st.button("对比"):
        import json

        paper_path = session_dir / selected_paper / "final_report.json"
        bt_path = backtest_dir / selected_bt / "metrics.json"

        if paper_path.exists() and bt_path.exists():
            with open(paper_path, encoding="utf-8") as f:
                paper = json.load(f)
            with open(bt_path, encoding="utf-8") as f:
                bt = json.load(f)

            comparison = {
                "指标": ["交易数", "净盈亏", "总信号", "被拒绝信号"],
                "Paper": [
                    paper.get("accepted_trades", 0),
                    f"${paper.get('net_pnl', 0):.2f}",
                    paper.get("total_signals", 0),
                    paper.get("rejected_signals", 0),
                ],
                "Backtest": [
                    bt.get("trade_count", 0),
                    f"{bt.get('net_return', 0) * 100:.2f}%",
                    "-",
                    "-",
                ],
            }
            st.table(comparison)
        else:
            st.warning("找不到报告文件")
else:
    st.info("暂无可对比的数据")

conn.close()
