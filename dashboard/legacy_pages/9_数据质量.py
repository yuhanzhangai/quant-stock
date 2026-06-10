"""数据质量页面 — 数据覆盖范围 + 质量检查结果。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="数据质量", page_icon="📊", layout="wide")
st.title("📊 数据质量")

DB_PATH = Path("data/meta/research.duckdb")

if not DB_PATH.exists():
    st.warning("研究数据库未初始化")
    st.stop()

import duckdb

conn = duckdb.connect(str(DB_PATH), read_only=True)

# Data coverage
st.subheader("数据覆盖范围")
coverage = conn.execute(
    "SELECT symbol, timeframe, SUM(row_count) as total_rows, "
    "MIN(start_ts) as start, MAX(end_ts) as end "
    "FROM data_manifest WHERE dataset = 'ohlcv' "
    "GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
).fetchdf()

if len(coverage) > 0:
    st.dataframe(coverage, use_container_width=True, hide_index=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("币种数", coverage["symbol"].nunique())
    with col2:
        st.metric("总行数", f"{coverage['total_rows'].sum():,}")
    with col3:
        st.metric("时间框架", coverage["timeframe"].nunique())

# Quality checks
st.subheader("质量检查结果")

# Filter
symbol_filter = st.selectbox(
    "筛选币种", ["全部"] + sorted(coverage["symbol"].unique().tolist()) if len(coverage) > 0 else ["全部"]
)

if symbol_filter == "全部":
    dq = conn.execute(
        "SELECT symbol, timeframe, check_name, status, severity, issue_count, created_at "
        "FROM data_quality_checks ORDER BY created_at DESC LIMIT 100"
    ).fetchdf()
else:
    dq = conn.execute(
        "SELECT symbol, timeframe, check_name, status, severity, issue_count, created_at "
        "FROM data_quality_checks WHERE symbol = ? ORDER BY created_at DESC LIMIT 100",
        [symbol_filter],
    ).fetchdf()

if len(dq) > 0:
    # Summary metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("总检查数", len(dq))
    with col2:
        fails = (dq["status"] == "fail").sum()
        st.metric("失败", str(fails))
    with col3:
        warnings = (dq["status"] == "warning").sum()
        st.metric("警告", str(warnings))

    st.dataframe(dq, use_container_width=True, hide_index=True)
else:
    st.info("暂无检查记录。运行 `python scripts/run_data_quality.py --all --save-db`")

conn.close()
