"""研究总览页面 — 系统状态一览。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="研究总览", page_icon="🔬", layout="wide")
st.title("🔬 研究总览")

DB_PATH = Path("data/meta/research.duckdb")

if not DB_PATH.exists():
    st.warning("研究数据库未初始化，请运行 `python scripts/init_research_db.py`")
    st.stop()

import duckdb

conn = duckdb.connect(str(DB_PATH), read_only=True)

# Production strategies
st.subheader("当前 Production 策略")
prod = conn.execute(
    "SELECT strategy_name, strategy_version, direction, timeframe, symbols "
    "FROM strategy_registry WHERE status = 'production'"
).fetchdf()
if len(prod) > 0:
    st.dataframe(prod, use_container_width=True, hide_index=True)
else:
    st.info("暂无 Production 策略")

# Candidate strategies
st.subheader("Candidate 策略")
cand = conn.execute(
    "SELECT strategy_name, strategy_version, direction, timeframe, symbols "
    "FROM strategy_registry WHERE status = 'candidate'"
).fetchdf()
if len(cand) > 0:
    st.dataframe(cand, use_container_width=True, hide_index=True)

# Active experiments
st.subheader("进行中的实验")
exps = conn.execute(
    "SELECT experiment_name, strategy_name, status, created_at "
    "FROM experiment_runs WHERE status = 'created' OR status = 'running' "
    "ORDER BY created_at DESC LIMIT 10"
).fetchdf()
if len(exps) > 0:
    st.dataframe(exps, use_container_width=True, hide_index=True)
else:
    st.info("暂无进行中的实验")

# Recent backtests
st.subheader("最近回测")
bts = conn.execute(
    "SELECT strategy_name, symbol, timeframe, sharpe, trade_count, net_return, created_at "
    "FROM backtest_runs ORDER BY created_at DESC LIMIT 10"
).fetchdf()
if len(bts) > 0:
    st.dataframe(bts, use_container_width=True, hide_index=True)

# Recent validation
st.subheader("最近验证结果")
vals = conn.execute(
    "SELECT run_id, gate_name, status, score, threshold, created_at "
    "FROM validation_results ORDER BY created_at DESC LIMIT 20"
).fetchdf()
if len(vals) > 0:
    st.dataframe(vals, use_container_width=True, hide_index=True)

# Data quality warnings
st.subheader("数据质量警告")
dq = conn.execute(
    "SELECT symbol, timeframe, check_name, status, severity, issue_count "
    "FROM data_quality_checks WHERE status != 'pass' "
    "ORDER BY created_at DESC LIMIT 20"
).fetchdf()
if len(dq) > 0:
    st.dataframe(dq, use_container_width=True, hide_index=True)
else:
    st.success("无数据质量问题")

conn.close()
