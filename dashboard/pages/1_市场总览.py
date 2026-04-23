"""市场总览页面。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import plotly.express as px
import polars as pl

from config.settings import get_settings
from src.storage.parquet_writer import ParquetWriter

st.set_page_config(page_title="市场总览", page_icon="📈", layout="wide")
st.title("📈 市场总览")

settings = get_settings()
writer = ParquetWriter(settings.parquet_dir)


@st.cache_data(ttl=300)
def load_available_symbols() -> list[str]:
    """扫描已有数据的交易对。"""
    ohlcv_dir = settings.parquet_dir / "ohlcv" / "spot"
    if not ohlcv_dir.exists():
        return []
    return [d.name for d in ohlcv_dir.iterdir() if d.is_dir()]


@st.cache_data(ttl=60)
def load_latest_data(symbol: str, timeframe: str = "1h", bars: int = 200) -> pl.DataFrame:
    """加载最新 K 线数据。"""
    df = writer.read_ohlcv(symbol, timeframe)
    if df.is_empty():
        return df
    return df.tail(bars)


symbols = load_available_symbols()

if not symbols:
    st.warning("暂无数据，请先运行 `python scripts/bootstrap_data.py` 回填数据。")
    st.stop()

# 侧边栏
with st.sidebar:
    st.header("筛选")
    selected_symbols = st.multiselect("选择币种", symbols, default=symbols[:5])
    timeframe = st.selectbox("时间周期", ["1h", "4h", "1d"], index=0)

# 主内容
col1, col2 = st.columns(2)

with col1:
    st.subheader("涨跌排行")
    changes = []
    for sym in symbols:
        df = load_latest_data(sym, timeframe, bars=2)
        if len(df) >= 2:
            pct = (df["close"][-1] - df["close"][-2]) / df["close"][-2] * 100
            changes.append({"symbol": sym, "change_pct": round(pct, 2), "price": df["close"][-1]})

    if changes:
        changes_df = pl.DataFrame(changes).sort("change_pct", descending=True)
        st.dataframe(changes_df.to_pandas(), use_container_width=True, hide_index=True)

with col2:
    st.subheader("最新成交量")
    volumes = []
    for sym in selected_symbols:
        df = load_latest_data(sym, timeframe, bars=24)
        if not df.is_empty():
            avg_vol = df["volume"].mean()
            volumes.append({"symbol": sym, "avg_volume_24h": round(avg_vol, 2)})

    if volumes:
        vol_df = pl.DataFrame(volumes).sort("avg_volume_24h", descending=True)
        fig = px.bar(vol_df.to_pandas(), x="symbol", y="avg_volume_24h", title="平均成交量")
        st.plotly_chart(fig, use_container_width=True)

# K 线缩略图
st.subheader("K 线走势")
cols = st.columns(min(len(selected_symbols), 3))
for i, sym in enumerate(selected_symbols[:3]):
    with cols[i]:
        df = load_latest_data(sym, timeframe, bars=100)
        if not df.is_empty():
            pdf = df.to_pandas()
            pdf["datetime"] = pdf["timestamp"].apply(lambda x: pl.from_epoch(x, time_unit="ms"))
            fig = px.line(pdf, x="datetime", y="close", title=sym)
            fig.update_layout(height=300, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
