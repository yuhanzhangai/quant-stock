"""生成最终策略综合评估 HTML 报告。"""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    reports_dir = Path("reports")

    # 加载数据
    gen = pd.read_csv(reports_dir / "generalization_test.csv")
    btc = pd.read_csv(reports_dir / "optimize_4h_BTC-USDT.csv")
    eth = pd.read_csv(reports_dir / "optimize_4h_ETH-USDT.csv")

    btc = btc[btc["total_trades"] > 0]
    eth = eth[eth["total_trades"] > 0]

    # 创建报告
    fig = make_subplots(
        rows=3,
        cols=2,
        subplot_titles=[
            "BTC 4h: Sharpe by Strategy",
            "ETH 4h: Sharpe by Strategy",
            "Generalization: Sharpe across 5 Coins",
            "Generalization: Return vs Drawdown",
            "BTC: Return vs Sharpe (all params)",
            "ETH: Return vs Sharpe (all params)",
        ],
        vertical_spacing=0.08,
    )

    # 1. BTC best-of-each
    btc_best = btc.loc[btc.groupby("strategy")["sharpe_ratio"].idxmax()].sort_values("sharpe_ratio", ascending=True)
    fig.add_trace(
        go.Bar(
            y=btc_best["strategy"],
            x=btc_best["sharpe_ratio"],
            orientation="h",
            marker_color=["#F44336" if s < 0 else "#4CAF50" for s in btc_best["sharpe_ratio"]],
            text=[f"{s:.2f}" for s in btc_best["sharpe_ratio"]],
            textposition="outside",
        ),
        row=1,
        col=1,
    )

    # 2. ETH best-of-each
    eth_best = eth.loc[eth.groupby("strategy")["sharpe_ratio"].idxmax()].sort_values("sharpe_ratio", ascending=True)
    fig.add_trace(
        go.Bar(
            y=eth_best["strategy"],
            x=eth_best["sharpe_ratio"],
            orientation="h",
            marker_color=["#F44336" if s < 0 else "#2196F3" for s in eth_best["sharpe_ratio"]],
            text=[f"{s:.2f}" for s in eth_best["sharpe_ratio"]],
            textposition="outside",
        ),
        row=1,
        col=2,
    )

    # 3. Generalization heatmap-like
    gen_pivot = gen.pivot_table(values="sharpe_ratio", index="strategy", columns="symbol")
    for strat in gen_pivot.index:
        fig.add_trace(
            go.Bar(name=strat, x=gen_pivot.columns.tolist(), y=gen_pivot.loc[strat].values, showlegend=False),
            row=2,
            col=1,
        )

    # 4. Return vs Drawdown scatter
    fig.add_trace(
        go.Scatter(
            x=gen["max_drawdown_pct"],
            y=gen["total_return_pct"],
            mode="markers+text",
            text=gen["symbol"] + "<br>" + gen["strategy"],
            textposition="top center",
            textfont=dict(size=8),
            marker=dict(size=10, color=gen["sharpe_ratio"], colorscale="RdYlGn", showscale=True),
        ),
        row=2,
        col=2,
    )

    # 5-6. All params scatter
    for df, col_idx in [(btc, 1), (eth, 2)]:
        fig.add_trace(
            go.Scatter(
                x=df["sharpe_ratio"],
                y=df["total_return_pct"],
                mode="markers",
                marker=dict(size=4, opacity=0.4, color=df["max_drawdown_pct"], colorscale="Reds", showscale=False),
                text=df["strategy"],
                hovertemplate="%{text}<br>Sharpe: %{x:.2f}<br>Return: %{y:.1f}%",
            ),
            row=3,
            col=col_idx,
        )

    fig.update_layout(
        title="Crypto Quant Research - 13 Strategy Final Report (4h Timeframe)",
        height=1400,
        showlegend=False,
        template="plotly_white",
    )

    output = reports_dir / "final_strategy_report.html"
    fig.write_html(str(output))
    print(f"Final report saved to: {output}")


if __name__ == "__main__":
    main()
