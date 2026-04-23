"""做空策略大比拼：5 个做空策略 x 4 币种 x 3 段验证。

策略列表：
1. short_swing        — 基础做空（已有，基线）
2. short_momentum_break — 动量崩溃（追跌）
3. short_bounce_fade   — 熊市反弹做空（做空死猫反弹）
4. short_rsi_overbought — RSI 超买做空
5. short_trend_follow  — 趋势跟空（MA死叉+MACD死叉）

配置：
- 费率：OKX_SWAP（maker 0.02%, taker 0.05%）
- 资金：init_cash=250（$50 x 5x 杠杆）
- 频率：5min
- 做空模拟：反转价格 invert_price()
- 排名标准：4 币种 avg sharpe

运行：cd "C:/Users/tuhai/Desktop/crypto量化测试" && uv run python scripts/short_aggressive_backtest.py
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings
from src.strategies.short_swing import ShortSwingStrategy, invert_price
from src.strategies.short_momentum_break import ShortMomentumBreakStrategy
from src.strategies.short_bounce_fade import ShortBounceFadeStrategy
from src.strategies.short_rsi_overbought import ShortRSIOverboughtStrategy
from src.strategies.short_trend_follow import ShortTrendFollowStrategy


COINS = ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"]


def load_price(symbol: str, tf: str) -> pd.Series | None:
    """加载价格数据。"""
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, tf)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


def split_3_segments(price: pd.Series) -> list[tuple[str, pd.Series]]:
    """将价格序列均分 3 段。"""
    n = len(price)
    seg_len = n // 3
    segments = []
    for i, label in enumerate(["Seg1", "Seg2", "Seg3"]):
        start = i * seg_len
        end = (i + 1) * seg_len if i < 2 else n
        segments.append((label, price.iloc[start:end]))
    return segments


# 策略配置：(名称, 策略实例, 额外参数)
STRATEGIES = [
    ("short_swing", ShortSwingStrategy(), {}),
    ("short_swing_SOL", ShortSwingStrategy(), {"min_gap": 288, "rsi_entry": 55}),  # SOL 最优参数
    ("momentum_break", ShortMomentumBreakStrategy(), {}),
    ("momentum_break_tight", ShortMomentumBreakStrategy(), {"min_gap": 96, "trail_pct": 1.0}),  # 更激进
    ("bounce_fade", ShortBounceFadeStrategy(), {}),
    ("bounce_fade_wide", ShortBounceFadeStrategy(), {"min_gap": 144, "ma_proximity_pct": 1.5}),
    ("rsi_overbought", ShortRSIOverboughtStrategy(), {}),
    ("rsi_overbought_loose", ShortRSIOverboughtStrategy(), {"rsi_overbought": 65, "rsi_entry_cross": 60}),
    ("trend_follow", ShortTrendFollowStrategy(), {}),
    ("trend_follow_fast", ShortTrendFollowStrategy(), {"fast_ma": 36, "slow_ma": 120, "min_gap": 192}),
]


def main() -> None:
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")

    print("=" * 100)
    print("  做空策略大比拼 — 10 个策略变体 x 4 币种 x 3 段验证")
    print("  费率: OKX_SWAP | 初始资金: $250 ($50 x 5x) | 5min K线")
    print("  排名标准: avg Sharpe across all coins & segments")
    print("=" * 100)

    # 收集所有结果
    all_results: list[dict] = []

    for coin in COINS:
        price = load_price(coin, "5m")
        if price is None or len(price) < 500:
            print(f"\n{coin}: 数据不足，跳过")
            continue

        coin_short = coin.replace("-USDT", "")
        print(f"\n{'━' * 100}")
        print(f"  {coin}  |  数据量: {len(price)} 根  |  {price.index[0]} ~ {price.index[-1]}")
        print(f"  价格范围: ${price.min():.4f} ~ ${price.max():.4f}")
        print(f"{'━' * 100}")

        segments = split_3_segments(price)

        print(
            f"  {'策略':<24} | {'段':>4} | {'入场':>4} | "
            f"{'收益%':>8} | {'夏普':>6} | {'回撤%':>7} | "
            f"{'胜率%':>6} | {'终值$':>8}"
        )
        print(f"  {'─' * 90}")

        for strat_name, strat, extra_params in STRATEGIES:
            seg_results = []

            for seg_label, seg_price in segments:
                if len(seg_price) < 300:
                    print(f"  {strat_name:<24} | {seg_label:>4} | 数据不足")
                    continue

                try:
                    entries, exits = strat.generate_signals(seg_price, **extra_params)
                except Exception as e:
                    print(f"  {strat_name:<24} | {seg_label:>4} | ERROR: {e}")
                    continue

                n_entries = int(entries.sum())

                if n_entries == 0:
                    result = {
                        "strategy": strat_name,
                        "coin": coin_short,
                        "segment": seg_label,
                        "trades": 0,
                        "total_return_pct": 0.0,
                        "sharpe_ratio": 0.0,
                        "max_drawdown_pct": 0.0,
                        "win_rate_pct": 0.0,
                        "final_value": 250.0,
                    }
                    all_results.append(result)
                    print(
                        f"  {strat_name:<24} | {seg_label:>4} | {0:>4} | "
                        f"{'0.00':>8} | {'0.00':>6} | {'0.00':>7} | "
                        f"{'--':>6} | {'250.00':>8}"
                    )
                    continue

                # 反转价格模拟做空
                price_inv = invert_price(seg_price)
                pf = engine.run(price_inv, entries, exits)
                m = compute_metrics(pf)

                result = {
                    "strategy": strat_name,
                    "coin": coin_short,
                    "segment": seg_label,
                    "trades": n_entries,
                    "total_return_pct": m["total_return_pct"],
                    "sharpe_ratio": m["sharpe_ratio"],
                    "max_drawdown_pct": m["max_drawdown_pct"],
                    "win_rate_pct": m["win_rate_pct"],
                    "final_value": m["final_value"],
                }
                all_results.append(result)

                ret = m["total_return_pct"]
                sharpe = m["sharpe_ratio"]
                dd = m["max_drawdown_pct"]
                wr = m["win_rate_pct"]
                fv = m["final_value"]

                # 用颜色标记盈亏（终端 ANSI）
                ret_str = f"{ret:>+8.2f}"
                print(
                    f"  {strat_name:<24} | {seg_label:>4} | {n_entries:>4} | "
                    f"{ret_str} | {sharpe:>6.2f} | {dd:>7.2f} | "
                    f"{wr:>6.1f} | {fv:>8.2f}"
                )

            print(f"  {'─' * 90}")

    # === 总排名 ===
    if not all_results:
        print("\n没有任何回测结果！")
        return

    df = pd.DataFrame(all_results)

    print(f"\n\n{'=' * 100}")
    print("  综合排名 — 按 Avg Sharpe Ratio (所有币种+段)")
    print("=" * 100)

    # 按策略聚合
    agg = df.groupby("strategy").agg(
        avg_sharpe=("sharpe_ratio", "mean"),
        avg_return=("total_return_pct", "mean"),
        avg_dd=("max_drawdown_pct", "mean"),
        avg_winrate=("win_rate_pct", "mean"),
        total_trades=("trades", "sum"),
        positive_segments=("total_return_pct", lambda x: (x > 0).sum()),
        total_segments=("total_return_pct", "count"),
    ).sort_values("avg_sharpe", ascending=False)

    print(
        f"\n  {'排名':>4} | {'策略':<24} | {'Avg夏普':>8} | {'Avg收益%':>9} | "
        f"{'Avg回撤%':>8} | {'Avg胜率%':>8} | {'总交易':>6} | {'正收益段':>8}"
    )
    print(f"  {'─' * 95}")

    for rank, (strat_name, row) in enumerate(agg.iterrows(), 1):
        pos_str = f"{int(row['positive_segments'])}/{int(row['total_segments'])}"
        print(
            f"  {rank:>4} | {strat_name:<24} | {row['avg_sharpe']:>+8.3f} | "
            f"{row['avg_return']:>+9.2f} | {row['avg_dd']:>8.2f} | "
            f"{row['avg_winrate']:>8.1f} | {int(row['total_trades']):>6} | {pos_str:>8}"
        )

    # === 按币种排名 ===
    print(f"\n\n{'=' * 100}")
    print("  按币种最佳策略")
    print("=" * 100)

    for coin_short in ["ETH", "SOL", "NEAR", "ARB"]:
        coin_df = df[df["coin"] == coin_short]
        if coin_df.empty:
            continue

        coin_agg = coin_df.groupby("strategy").agg(
            avg_sharpe=("sharpe_ratio", "mean"),
            avg_return=("total_return_pct", "mean"),
            positive_segs=("total_return_pct", lambda x: (x > 0).sum()),
            total_segs=("total_return_pct", "count"),
        ).sort_values("avg_sharpe", ascending=False)

        print(f"\n  {coin_short}:")
        for rank, (strat_name, row) in enumerate(coin_agg.head(3).iterrows(), 1):
            pos_str = f"{int(row['positive_segs'])}/{int(row['total_segs'])}"
            print(
                f"    #{rank} {strat_name:<24} | Sharpe={row['avg_sharpe']:>+.3f} | "
                f"Ret={row['avg_return']:>+.2f}% | 正收益={pos_str}"
            )

    # === 一致性分析：哪个策略在最多段上正收益 ===
    print(f"\n\n{'=' * 100}")
    print("  一致性排名 — 正收益段数占比")
    print("=" * 100)

    consistency = df.groupby("strategy").apply(
        lambda x: pd.Series({
            "positive_pct": (x["total_return_pct"] > 0).mean() * 100,
            "positive_count": (x["total_return_pct"] > 0).sum(),
            "total_count": len(x),
            "avg_sharpe": x["sharpe_ratio"].mean(),
        })
    ).sort_values("positive_pct", ascending=False)

    for strat_name, row in consistency.iterrows():
        pct = row["positive_pct"]
        cnt = int(row["positive_count"])
        total = int(row["total_count"])
        print(
            f"  {strat_name:<24} | 正收益: {cnt}/{total} ({pct:.0f}%) | "
            f"Avg Sharpe: {row['avg_sharpe']:>+.3f}"
        )

    print(f"\n{'=' * 100}")
    print("  做空策略回测完成！")
    print(f"{'=' * 100}")


if __name__ == "__main__":
    main()
