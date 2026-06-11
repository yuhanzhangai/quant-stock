"""K 线图 + 策略回测可视化面板。

功能：
1. 交互式 K 线图（蜡烛图 + 成交量）
2. 策略信号标注（入场/出场点）
3. 所有策略可选测试
4. 实时回测结果（夏普/收益/回撤）
5. 指标叠加（MA/RSI/MACD/BB）
6. 多币种多周期切换
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from config.settings import get_settings
from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from src.strategies.combo.fast_exit import fast_exit_signal
from src.strategies.extreme_reversal import extreme_reversal_signal
from src.strategies.intraday_momentum import intraday_momentum_signal

# Strategy imports
from src.strategies.minute_swing import minute_swing_signal
from src.strategies.minute_swing_dual import minute_swing_dual_signal

st.set_page_config(page_title="策略回测", page_icon="📊", layout="wide")
st.title("📊 K线图 + 策略回测")

settings = get_settings()
writer = ParquetWriter(settings.parquet_dir)

# === 侧边栏 ===
with st.sidebar:
    st.header("基本设置")

    # 币种
    all_coins = []
    ohlcv_dir = settings.parquet_dir / "ohlcv" / "spot"
    if ohlcv_dir.exists():
        all_coins = sorted(set(d.name for d in ohlcv_dir.iterdir() if d.is_dir()))
    coin = st.selectbox("币种", all_coins, index=all_coins.index("ETH-USDT") if "ETH-USDT" in all_coins else 0)

    # 时间周期
    available_tfs = []
    for tf in ["1m", "5m", "15m", "1h", "4h", "1d"]:
        test_df = writer.read_ohlcv(coin, tf)
        if not test_df.is_empty():
            available_tfs.append(tf)
    timeframe = st.selectbox("时间周期", available_tfs, index=available_tfs.index("5m") if "5m" in available_tfs else 0)

    # 时间区间
    st.header("回测区间")

    # 先加载全部数据获取时间范围
    _full_df = writer.read_ohlcv(coin, timeframe)
    _full_pdf = _full_df.to_pandas()
    _full_pdf["datetime"] = pd.to_datetime(_full_pdf["timestamp"], unit="ms", utc=True)
    _data_start = _full_pdf["datetime"].min()
    _data_end = _full_pdf["datetime"].max()

    col_s, col_e = st.columns(2)
    with col_s:
        start_date = st.date_input("开始日期", value=_data_start.date())
    with col_e:
        end_date = st.date_input("结束日期", value=_data_end.date())

    col_sh, col_eh = st.columns(2)
    with col_sh:
        start_hour = st.number_input("开始时(UTC)", 0, 23, 0)
    with col_eh:
        end_hour = st.number_input("结束时(UTC)", 0, 23, 23)

    # 快捷按钮
    col_q1, col_q2, col_q3 = st.columns(3)
    with col_q1:
        if st.button("最近1周"):
            start_date = (_data_end - pd.Timedelta(days=7)).date()
    with col_q2:
        if st.button("最近1月"):
            start_date = (_data_end - pd.Timedelta(days=30)).date()
    with col_q3:
        if st.button("全部数据"):
            start_date = _data_start.date()

    # 策略
    st.header("策略选择")
    strategy_name = st.selectbox(
        "策略",
        [
            "MinSwing v3",
            "MinSwing + FastExit (ETH专用)",
            "MinSwing 双向",
            "极端反转抄底",
            "日内动量",
            "无策略（纯图表）",
        ],
    )

    # 策略参数
    st.header("策略参数")
    trend_ma = st.slider("趋势均线周期", 50, 300, 180)
    stop_pct = st.slider("止损 %", 0.5, 5.0, 2.0, 0.5)
    take_profit = st.slider("止盈 %", 2.0, 20.0, 8.0, 0.5)
    min_gap = st.slider("最小间隔（K线根数）", 10, 300, 144)

    # 指标
    st.header("技术指标")
    show_ma = st.checkbox("均线 MA", True)
    show_rsi = st.checkbox("RSI 相对强弱", True)
    show_macd = st.checkbox("MACD 指标", False)
    show_bb = st.checkbox("布林带 BB", False)
    show_volume = st.checkbox("成交量", True)

    # 资金
    st.header("资金设置")
    leverage = st.selectbox("杠杆倍数", [1, 3, 5, 10], index=2)
    capital = st.number_input("本金 ($)", value=50, min_value=1)

# === 加载数据（按时间区间筛选）===
df = writer.read_ohlcv(coin, timeframe)
if df.is_empty():
    st.error(f"{coin} {timeframe} 无数据")
    st.stop()

pdf = df.to_pandas()
pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
pdf = pdf.set_index("datetime").sort_index()

# 按时间区间筛选
start_dt = pd.Timestamp(f"{start_date} {start_hour:02d}:00:00", tz="UTC")
end_dt = pd.Timestamp(f"{end_date} {end_hour:02d}:59:59", tz="UTC")
pdf = pdf.loc[start_dt:end_dt]

if pdf.empty:
    st.error("所选区间无数据")
    st.stop()

st.caption(
    f"数据范围: {pdf.index[0].strftime('%Y-%m-%d %H:%M')} ~ {pdf.index[-1].strftime('%Y-%m-%d %H:%M')} | {len(pdf)} 根K线"
)

price = pdf["close"]

# === Run Strategy ===
entries = pd.Series(False, index=price.index)
exits = pd.Series(False, index=price.index)
strategy_params = dict(trend_ma=trend_ma, stop_pct=stop_pct, take_profit_pct=take_profit, min_gap=min_gap)

if strategy_name == "MinSwing v3":
    entries, exits = minute_swing_signal(price, **strategy_params)
elif strategy_name == "MinSwing + FastExit (ETH专用)":
    entries, exits = fast_exit_signal(price, fast_ma=90, profit_thr=0.3, **strategy_params)
elif strategy_name == "MinSwing 双向":
    entries, exits = minute_swing_dual_signal(price, **strategy_params)
elif strategy_name == "极端反转抄底":
    entries, exits = extreme_reversal_signal(price, drop_threshold=-5.0)
elif strategy_name == "日内动量":
    entries, exits = intraday_momentum_signal(price, session_bars=96, momentum_threshold=0.008)

# === Backtest ===
if strategy_name != "无策略（纯图表）":
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=capital * leverage, freq=timeframe)
    portfolio = engine.run(price, entries, exits)
    metrics = compute_metrics(portfolio)

    # Metrics display
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Sharpe", f"{metrics['sharpe_ratio']:.3f}")
    col2.metric("Return", f"{metrics['total_return_pct']:.1f}%")
    col3.metric("Max DD", f"{metrics['max_drawdown_pct']:.1f}%")
    col4.metric("Win Rate", f"{metrics['win_rate_pct']:.0f}%")
    col5.metric("Trades", f"{metrics['total_trades']}")

    # PnL on capital
    pnl = metrics.get("final_value", capital * leverage) - capital * leverage
    pnl_pct = pnl / capital * 100
    st.info(f"${capital} x {leverage}x = ${capital * leverage} position | P&L: ${pnl:.2f} ({pnl_pct:+.1f}% on capital)")

# === Calculate Indicators ===
ma_short = price.rolling(window=min(trend_ma // 3, 60)).mean()
ma_long = price.rolling(window=trend_ma).mean()

delta = price.diff()
gains = delta.clip(lower=0).rolling(14).mean()
losses = (-delta).clip(lower=0).rolling(14).mean()
rs = gains / losses
rsi = 100 - (100 / (1 + rs))

ema12 = price.ewm(span=12, adjust=False).mean()
ema26 = price.ewm(span=26, adjust=False).mean()
macd_line = ema12 - ema26
signal_line = macd_line.ewm(span=9, adjust=False).mean()
macd_hist = macd_line - signal_line

bb_mid = price.rolling(20).mean()
bb_std = price.rolling(20).std()
bb_upper = bb_mid + 2 * bb_std
bb_lower = bb_mid - 2 * bb_std

# === Build Chart ===
n_rows = 1 + show_rsi + show_macd + show_volume
row_heights = [0.5]
subplot_titles = [f"{coin} {timeframe}"]
if show_volume:
    row_heights.append(0.1)
    subplot_titles.append("Volume")
if show_rsi:
    row_heights.append(0.15)
    subplot_titles.append("RSI")
if show_macd:
    row_heights.append(0.15)
    subplot_titles.append("MACD")

fig = make_subplots(
    rows=n_rows,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.03,
    subplot_titles=subplot_titles,
    row_heights=row_heights,
)

# Candlestick
fig.add_trace(
    go.Candlestick(
        x=pdf.index,
        open=pdf["open"],
        high=pdf["high"],
        low=pdf["low"],
        close=pdf["close"],
        name="Price",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
    ),
    row=1,
    col=1,
)

# MAs
if show_ma:
    fig.add_trace(
        go.Scatter(x=price.index, y=ma_short, name=f"MA{trend_ma // 3}", line=dict(color="#FF9800", width=1)),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=price.index, y=ma_long, name=f"MA{trend_ma}", line=dict(color="#2196F3", width=1.5)), row=1, col=1
    )

# Bollinger Bands
if show_bb:
    fig.add_trace(
        go.Scatter(x=price.index, y=bb_upper, name="BB Upper", line=dict(color="gray", width=0.5, dash="dot")),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=price.index,
            y=bb_lower,
            name="BB Lower",
            line=dict(color="gray", width=0.5, dash="dot"),
            fill="tonexty",
            fillcolor="rgba(128,128,128,0.05)",
        ),
        row=1,
        col=1,
    )

# Entry/Exit markers
if strategy_name != "无策略（纯图表）":
    entry_pts = entries[entries]
    exit_pts = exits[exits]

    if len(entry_pts) > 0:
        fig.add_trace(
            go.Scatter(
                x=entry_pts.index,
                y=price[entry_pts.index],
                mode="markers",
                name="Entry",
                marker=dict(symbol="triangle-up", size=12, color="#00E676"),
            ),
            row=1,
            col=1,
        )

    if len(exit_pts) > 0:
        fig.add_trace(
            go.Scatter(
                x=exit_pts.index,
                y=price[exit_pts.index],
                mode="markers",
                name="Exit",
                marker=dict(symbol="triangle-down", size=12, color="#FF1744"),
            ),
            row=1,
            col=1,
        )

current_row = 2

# Volume
if show_volume:
    colors = ["#26a69a" if c >= o else "#ef5350" for o, c in zip(pdf["open"], pdf["close"], strict=False)]
    fig.add_trace(
        go.Bar(x=pdf.index, y=pdf["volume"], name="Volume", marker_color=colors, opacity=0.5), row=current_row, col=1
    )
    current_row += 1

# RSI
if show_rsi:
    fig.add_trace(
        go.Scatter(x=rsi.index, y=rsi, name="RSI", line=dict(color="#9C27B0", width=1)), row=current_row, col=1
    )
    fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.3, row=current_row, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.3, row=current_row, col=1)
    current_row += 1

# MACD
if show_macd:
    fig.add_trace(
        go.Scatter(x=macd_line.index, y=macd_line, name="MACD", line=dict(color="#2196F3", width=1)),
        row=current_row,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=signal_line.index, y=signal_line, name="Signal", line=dict(color="#FF9800", width=1)),
        row=current_row,
        col=1,
    )
    colors = ["#26a69a" if v >= 0 else "#ef5350" for v in macd_hist]
    fig.add_trace(
        go.Bar(x=macd_hist.index, y=macd_hist, name="Histogram", marker_color=colors, opacity=0.5),
        row=current_row,
        col=1,
    )

# Layout
fig.update_layout(
    height=200 + 250 * n_rows,
    xaxis_rangeslider_visible=False,
    template="plotly_dark",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=50, r=50, t=80, b=50),
    dragmode="zoom",  # 默认拖拽缩放
)

# 所有 x 轴启用滚轮缩放
for i in range(1, n_rows + 1):
    axis_name = f"xaxis{i}" if i > 1 else "xaxis"
    fig.update_layout(**{axis_name: dict(type="date")})

# 所有 y 轴固定不缩放（只缩放时间轴）
for i in range(1, n_rows + 1):
    axis_name = f"yaxis{i}" if i > 1 else "yaxis"
    fig.update_layout(**{axis_name: dict(fixedrange=False)})

# 渲染图表，启用滚轮缩放
chart_config = {
    "scrollZoom": True,  # 滚轮缩放
    "displayModeBar": True,  # 显示工具栏
    "modeBarButtonsToAdd": [
        "drawline",
        "drawopenpath",
        "eraseshape",  # 画线工具
    ],
    "modeBarButtonsToRemove": ["lasso2d"],
}
st.plotly_chart(fig, use_container_width=True, config=chart_config)

# === Trade Log ===
if strategy_name != "无策略（纯图表）":
    st.subheader("Trade Log")
    entry_idx = entries[entries].index
    exit_idx = exits[exits].index

    trades = []
    for ei in entry_idx:
        nx = exit_idx[exit_idx > ei]
        if len(nx) == 0:
            continue
        xi = nx[0]
        ep = price.loc[ei]
        xp = price.loc[xi]
        pnl = (xp - ep) / ep * 100
        trades.append(
            {
                "Entry Time": ei.strftime("%Y-%m-%d %H:%M"),
                "Exit Time": xi.strftime("%Y-%m-%d %H:%M"),
                "Entry $": f"{ep:.2f}",
                "Exit $": f"{xp:.2f}",
                "P&L %": f"{pnl:+.2f}%",
                "Result": "WIN" if pnl > 0 else "LOSS",
            }
        )

    if trades:
        trade_df = pd.DataFrame(trades)
        st.dataframe(trade_df, use_container_width=True, hide_index=True)
        wins = sum(1 for t in trades if t["Result"] == "WIN")
        st.caption(
            f"{len(trades)} trades | {wins} wins ({wins / len(trades) * 100:.0f}%) | {len(trades) - wins} losses"
        )
    else:
        st.info("No trades in this period")

# === Equity Curve ===
if strategy_name != "无策略（纯图表）":
    st.subheader("Equity Curve")
    try:
        equity = portfolio.value()
        if isinstance(equity, pd.DataFrame):
            equity = equity.iloc[:, 0]
        fig_eq = go.Figure()
        fig_eq.add_trace(
            go.Scatter(x=equity.index, y=equity.values, name="Equity", fill="tozeroy", line=dict(color="#4CAF50"))
        )
        fig_eq.update_layout(height=250, template="plotly_dark", margin=dict(l=50, r=50, t=30, b=30))
        st.plotly_chart(fig_eq, use_container_width=True)
    except Exception:
        pass
