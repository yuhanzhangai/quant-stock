"""策略门禁页面 — 每个策略的 gate 状态。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="策略门禁", page_icon="🚦", layout="wide")
st.title("🚦 策略门禁")

DB_PATH = Path("data/meta/research.duckdb")

if not DB_PATH.exists():
    st.warning("研究数据库未初始化")
    st.stop()

import duckdb

conn = duckdb.connect(str(DB_PATH), read_only=True)

# Get validation results grouped by run_id
runs = conn.execute("SELECT DISTINCT run_id FROM validation_results ORDER BY run_id").fetchdf()

if len(runs) == 0:
    st.info("暂无验证记录。运行 `python scripts/validate_strategy.py` 生成。")
    st.stop()

selected_run = st.selectbox("选择验证运行", runs["run_id"].tolist())

# Show gate results for selected run
gates = conn.execute(
    "SELECT gate_name, status, score, threshold FROM validation_results WHERE run_id = ? ORDER BY created_at",
    [selected_run],
).fetchdf()

if len(gates) > 0:
    # Color coding
    def color_status(val: str) -> str:
        """给状态上色。"""
        colors = {
            "pass": "background-color: #4CAF50",
            "fail": "background-color: #F44336",
            "warning": "background-color: #FF9800",
            "skipped": "background-color: #9E9E9E",
        }
        return colors.get(val, "")

    st.dataframe(
        gates.style.map(color_status, subset=["status"]),
        use_container_width=True,
        hide_index=True,
    )

    # Summary
    passed = (gates["status"] == "pass").sum()
    failed = (gates["status"] == "fail").sum()
    total = len(gates)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("通过", f"{passed}/{total}")
    with col2:
        st.metric("失败", str(failed))
    with col3:
        overall = "PASS" if failed == 0 else "FAIL"
        st.metric("总体", overall)

conn.close()
