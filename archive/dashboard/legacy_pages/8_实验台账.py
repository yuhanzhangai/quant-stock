"""实验台账页面 — 查看所有实验状态。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="实验台账", page_icon="📋", layout="wide")
st.title("📋 实验台账")

DB_PATH = Path("data/meta/research.duckdb")

if not DB_PATH.exists():
    st.warning("研究数据库未初始化")
    st.stop()

import duckdb

conn = duckdb.connect(str(DB_PATH), read_only=True)

# Filter by status
status_filter = st.selectbox(
    "筛选状态", ["全部", "created", "running", "completed", "rejected", "accepted", "inconclusive"]
)

if status_filter == "全部":
    exps = conn.execute(
        "SELECT experiment_name, strategy_name, status, hypothesis, conclusion, created_at "
        "FROM experiment_runs ORDER BY created_at DESC"
    ).fetchdf()
else:
    exps = conn.execute(
        "SELECT experiment_name, strategy_name, status, hypothesis, conclusion, created_at "
        "FROM experiment_runs WHERE status = ? ORDER BY created_at DESC",
        [status_filter],
    ).fetchdf()

if len(exps) > 0:
    st.dataframe(exps, use_container_width=True, hide_index=True)
    st.metric("总实验数", len(exps))
else:
    st.info("暂无实验记录")

conn.close()
