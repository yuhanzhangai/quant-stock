"""资金费率监控页面。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import plotly.express as px
import polars as pl
import streamlit as st

from config.settings import get_settings

st.set_page_config(page_title="资金费监控", page_icon="💰", layout="wide")
st.title("💰 资金费监控")

settings = get_settings()
funding_dir = settings.parquet_dir / "funding"


@st.cache_data(ttl=60)
def load_funding_data(symbol: str) -> pl.DataFrame:
    """加载资金费率数据。"""
    path = funding_dir / f"{symbol}.parquet"
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path).sort("funding_time")


@st.cache_data(ttl=300)
def get_available_funding_symbols() -> list[str]:
    """获取有资金费率数据的合约。"""
    if not funding_dir.exists():
        return []
    return [f.stem for f in funding_dir.glob("*.parquet")]


symbols = get_available_funding_symbols()

if not symbols:
    st.warning("暂无资金费率数据，请先运行 `python scripts/bootstrap_data.py` 回填数据。")
    st.stop()

# 侧边栏
with st.sidebar:
    selected = st.selectbox("选择合约", symbols)
    threshold = st.slider("异常阈值 (%)", 0.01, 0.5, 0.1, 0.01)

# 加载数据
df = load_funding_data(selected)

if df.is_empty():
    st.warning(f"无 {selected} 数据")
    st.stop()

# 当前资金费率
latest = df.tail(1)
current_rate = latest["funding_rate"][0]
st.metric(
    label=f"{selected} 当前资金费率",
    value=f"{current_rate:.6f}",
    delta=f"{current_rate * 100:.4f}%",
)

# 历史曲线
st.subheader("资金费率历史")
pdf = df.to_pandas()
pdf["datetime"] = pd.to_datetime(pdf["funding_time"], unit="ms", utc=True)
pdf["rate_pct"] = pdf["funding_rate"] * 100

fig = px.line(pdf, x="datetime", y="rate_pct", title=f"{selected} 资金费率 (%)")
fig.add_hline(y=threshold, line_dash="dash", line_color="red", annotation_text=f"+{threshold}%")
fig.add_hline(y=-threshold, line_dash="dash", line_color="green", annotation_text=f"-{threshold}%")
fig.update_layout(height=400)
st.plotly_chart(fig, use_container_width=True)

# 异常标记
st.subheader("异常资金费率")
threshold_decimal = threshold / 100
anomalies = (
    df.filter(pl.col("funding_rate").abs() > threshold_decimal)
    .with_columns(
        (pl.col("funding_time").cast(pl.Datetime("ms"))).alias("datetime"),
        (pl.col("funding_rate") * 100).round(4).alias("rate_pct"),
    )
    .select(["datetime", "funding_rate", "rate_pct"])
)

if anomalies.is_empty():
    st.info(f"无异常资金费率（阈值: ±{threshold}%）")
else:
    st.dataframe(anomalies.to_pandas(), use_container_width=True, hide_index=True)

# 统计
st.subheader("统计")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("平均费率", f"{df['funding_rate'].mean():.6f}")
with col2:
    st.metric("最大费率", f"{df['funding_rate'].max():.6f}")
with col3:
    st.metric("最小费率", f"{df['funding_rate'].min():.6f}")
with col4:
    st.metric("数据条数", f"{len(df)}")
