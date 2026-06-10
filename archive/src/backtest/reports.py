"""回测报告生成。"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import vectorbt as vbt
from loguru import logger
from plotly.subplots import make_subplots

from src.backtest.metrics import compute_metrics


def generate_report(
    portfolio: vbt.Portfolio,
    title: str = "Backtest Report",
    output_dir: Path = Path("reports"),
) -> Path:
    """生成回测报告 HTML。

    包含：累计收益曲线、回撤曲线、月度收益热图。

    Args:
        portfolio: vectorbt Portfolio 对象
        title: 报告标题
        output_dir: 输出目录

    Returns:
        报告文件路径
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = compute_metrics(portfolio)

    # 创建多子图
    fig = make_subplots(
        rows=3,
        cols=1,
        subplot_titles=["累计收益", "回撤", "权益曲线"],
        vertical_spacing=0.08,
        row_heights=[0.4, 0.3, 0.3],
    )

    # 1. 累计收益曲线
    cum_returns = portfolio.cumulative_returns()
    if isinstance(cum_returns, pd.DataFrame):
        cum_returns = cum_returns.iloc[:, 0]
    fig.add_trace(
        go.Scatter(
            x=cum_returns.index,
            y=cum_returns.values * 100,
            name="累计收益 %",
            line=dict(color="#2196F3"),
        ),
        row=1,
        col=1,
    )

    # 2. 回撤曲线
    drawdown = portfolio.drawdown()
    if isinstance(drawdown, pd.DataFrame):
        drawdown = drawdown.iloc[:, 0]
    fig.add_trace(
        go.Scatter(
            x=drawdown.index,
            y=drawdown.values * 100,
            name="回撤 %",
            fill="tozeroy",
            line=dict(color="#F44336"),
        ),
        row=2,
        col=1,
    )

    # 3. 权益曲线
    equity = portfolio.value()
    if isinstance(equity, pd.DataFrame):
        equity = equity.iloc[:, 0]
    fig.add_trace(
        go.Scatter(
            x=equity.index,
            y=equity.values,
            name="权益",
            line=dict(color="#4CAF50"),
        ),
        row=3,
        col=1,
    )

    # 添加指标注释
    metrics_text = (
        f"总收益: {metrics['total_return_pct']:.2f}% | "
        f"夏普: {metrics['sharpe_ratio']:.3f} | "
        f"最大回撤: {metrics['max_drawdown_pct']:.2f}% | "
        f"胜率: {metrics['win_rate_pct']:.1f}% | "
        f"交易次数: {metrics['total_trades']}"
    )

    fig.update_layout(
        title=f"{title}<br><sub>{metrics_text}</sub>",
        height=900,
        showlegend=True,
        template="plotly_white",
    )

    # 保存
    filename = title.replace(" ", "_").lower() + ".html"
    filepath = output_dir / filename
    fig.write_html(str(filepath))
    logger.info(f"回测报告已生成: {filepath}")

    return filepath
