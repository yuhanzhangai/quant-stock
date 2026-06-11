"""回测查看页面 — 从 DB 索引回测结果。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="回测查看", page_icon="📊", layout="wide")
st.title("📊 回测查看")

DB_PATH = Path("data/meta/research.duckdb")

if not DB_PATH.exists():
    st.warning("研究数据库未初始化，请运行 `python scripts/init_research_db.py`")
    st.stop()

import duckdb

conn = duckdb.connect(str(DB_PATH), read_only=True)

# List backtests from DB
runs = conn.execute(
    "SELECT backtest_id, run_id, strategy_name, symbol, timeframe, "
    "sharpe, net_return, trade_count, run_type, output_dir, created_at "
    "FROM backtest_runs ORDER BY created_at DESC LIMIT 100"
).fetchdf()

if len(runs) == 0:
    st.warning("暂无回测记录，请先运行 `python scripts/run_backtest.py`")
    conn.close()
    st.stop()

# Show backtest list
st.subheader("回测记录")
st.dataframe(runs, use_container_width=True, hide_index=True)

# Select a run to view details
selected_idx = st.selectbox(
    "选择回测查看详情",
    range(len(runs)),
    format_func=lambda i: (
        f"{runs.iloc[i]['run_id']} | {runs.iloc[i]['strategy_name']} | sharpe={runs.iloc[i]['sharpe']}"
    ),
)

if selected_idx is not None:
    row = runs.iloc[selected_idx]
    output_dir = row.get("output_dir", "")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Sharpe", f"{row['sharpe']:.4f}" if row["sharpe"] else "N/A")
    with col2:
        st.metric("净收益", f"{row['net_return']:.4%}" if row["net_return"] else "N/A")
    with col3:
        st.metric("交易数", str(row["trade_count"]))
    with col4:
        st.metric("类型", str(row.get("run_type", "single")))

    # Load artifacts from output_dir if available
    if output_dir and Path(output_dir).exists():
        st.subheader("Artifacts")

        # metrics.json
        metrics_path = Path(output_dir) / "metrics.json"
        if metrics_path.exists():
            with open(metrics_path, encoding="utf-8") as f:
                metrics = json.load(f)
            st.json(metrics)

        # HTML report (legacy support)
        html_path = Path(output_dir) / "html_report.html"
        if html_path.exists():
            with open(html_path, encoding="utf-8") as f:
                st.components.v1.html(f.read(), height=950, scrolling=True)
    elif output_dir:
        st.warning(f"DB 有记录但 artifact 目录缺失: {output_dir}")
    else:
        st.info("此回测无 artifact 目录 (可能是 grid_candidate)")

conn.close()
