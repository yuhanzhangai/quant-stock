"""因子表现页面。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import plotly.graph_objects as go
import polars as pl
import streamlit as st
from plotly.subplots import make_subplots

# 导入因子注册
import src.factors.technical  # noqa: F401
from config.settings import get_settings
from src.factors.registry import compute_all
from src.storage.parquet_writer import ParquetWriter

st.set_page_config(page_title="因子表现", page_icon="🔬", layout="wide")
st.title("🔬 因子表现")

settings = get_settings()
writer = ParquetWriter(settings.parquet_dir)


@st.cache_data(ttl=300)
def load_available_symbols() -> list[str]:
    ohlcv_dir = settings.parquet_dir / "ohlcv" / "spot"
    if not ohlcv_dir.exists():
        return []
    return sorted([d.name for d in ohlcv_dir.iterdir() if d.is_dir()])


@st.cache_data(ttl=300)
def get_available_timeframes(symbol: str) -> list[str]:
    """获取该币种有数据的时间周期。"""
    available = []
    for tf in ["1m", "5m", "15m", "1h", "4h", "1d"]:
        df = writer.read_ohlcv(symbol, tf)
        if not df.is_empty():
            available.append(tf)
    return available


@st.cache_data(ttl=60)
def load_and_compute(symbol: str, timeframe: str) -> pl.DataFrame:
    df = writer.read_ohlcv(symbol, timeframe)
    if df.is_empty():
        return df
    return compute_all(df)


def get_computed_factors(pdf: pd.DataFrame) -> list[str]:
    """获取实际计算出来的因子列（排除原始数据列）。"""
    base_cols = {"timestamp", "open", "high", "low", "close", "volume", "symbol", "datetime"}
    return [c for c in pdf.columns if c not in base_cols and not pdf[c].isna().all()]


symbols = load_available_symbols()

if not symbols:
    st.warning("暂无数据，请先运行 `python scripts/bootstrap_data.py`")
    st.stop()

# 侧边栏 - 先选币种，再动态加载可用选项
with st.sidebar:
    symbol = st.selectbox("选择币种", symbols)

    # 动态时间周期
    available_tfs = get_available_timeframes(symbol)
    if not available_tfs:
        st.warning(f"{symbol} 无数据")
        st.stop()
    timeframe = st.selectbox(
        "时间周期",
        available_tfs,
        index=min(len(available_tfs) - 1, available_tfs.index("1h") if "1h" in available_tfs else 0),
    )

# 加载并计算
df = load_and_compute(symbol, timeframe)

if df.is_empty():
    st.warning(f"无 {symbol} {timeframe} 数据")
    st.stop()

pdf = df.to_pandas()
pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)

# 动态因子列表 - 只显示实际计算出来的因子
computed_factors = get_computed_factors(pdf)

if not computed_factors:
    st.warning("无可用因子")
    st.stop()

with st.sidebar:
    selected_factors = st.multiselect(
        "选择因子",
        computed_factors,
        default=computed_factors[: min(2, len(computed_factors))],
    )

# 因子 + 价格叠加图
if selected_factors:
    st.subheader(f"{symbol} 因子与价格")

    fig = make_subplots(
        rows=len(selected_factors) + 1,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        subplot_titles=["Price"] + selected_factors,
        row_heights=[0.3] + [0.7 / len(selected_factors)] * len(selected_factors),
    )

    fig.add_trace(
        go.Scatter(x=pdf["datetime"], y=pdf["close"], name="Close", line=dict(color="#2196F3")),
        row=1,
        col=1,
    )

    colors = ["#FF9800", "#4CAF50", "#F44336", "#9C27B0", "#00BCD4"]
    for i, factor in enumerate(selected_factors):
        if factor in pdf.columns:
            fig.add_trace(
                go.Scatter(
                    x=pdf["datetime"],
                    y=pdf[factor],
                    name=factor,
                    line=dict(color=colors[i % len(colors)]),
                ),
                row=i + 2,
                col=1,
            )

    fig.update_layout(height=200 + 200 * len(selected_factors), showlegend=True, template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)

# 因子统计
st.subheader("因子统计")
stats = []
for factor in computed_factors:
    series = pdf[factor].dropna()
    if len(series) > 0:
        current_val = series.iloc[-1]
        mean_val = series.mean()
        std_val = series.std()
        zscore = (current_val - mean_val) / std_val if std_val > 0 else 0
        stats.append(
            {
                "因子": factor,
                "当前值": round(current_val, 6),
                "均值": round(mean_val, 6),
                "标准差": round(std_val, 6),
                "当前Z-Score": round(zscore, 2),
            }
        )

if stats:
    st.dataframe(stats, use_container_width=True, hide_index=True)
