"""特斯拉新闻事件驱动回测 + 分析。

功能：
1. 拉取 TSLA-USDT-SWAP 数据（或从本地加载）
2. 对所有重大新闻事件做利好/利空因子检测
3. 回测新闻事件策略
4. 参数优化（网格搜索）
5. 输出详细报告
"""

import asyncio
import io
import sys
from pathlib import Path

# Windows GBK 编码问题修复
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import vectorbt as vbt
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategies.tsla_news_event import (
    TSLA_NEWS_EVENTS,
    TslaNewsEventStrategy,
    analyze_all_events,
)

from src.backtest.costs import OKX_SWAP
from src.backtest.metrics import compute_metrics


# =========================================================================
# 1. 数据加载
# =========================================================================
async def load_or_fetch_data(timeframe: str = "1h") -> pd.DataFrame:
    """加载本地数据，没有则从 OKX 拉取。

    Args:
        timeframe: K 线周期

    Returns:
        OHLCV DataFrame
    """
    local_path = Path(f"data/parquet/ohlcv/swap/TSLA-USDT/{timeframe}.parquet")

    if local_path.exists():
        df = pd.read_parquet(local_path)
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df = df.set_index("timestamp")
            df.index = pd.to_datetime(df.index, utc=True)
        logger.info(f"从本地加载 | {local_path} | {len(df)} 行")
        return df

    logger.info("本地无数据，开始从 OKX 拉取...")
    from scripts.tsla_fetch_data import fetch_tsla_ohlcv, save_tsla_data

    df = await fetch_tsla_ohlcv(timeframe=timeframe, days_back=365)
    if not df.empty:
        save_tsla_data(df, timeframe=timeframe)
    return df


# =========================================================================
# 2. 事件分析报告
# =========================================================================
def print_event_analysis(df: pd.DataFrame) -> None:
    """打印事件分析报告。"""
    print("\n" + "=" * 100)
    print("📊 特斯拉新闻事件 - 利好/利空因子检测报告")
    print("=" * 100)

    for _, row in df.iterrows():
        if not row.get("valid", False):
            status = "⚠️ 数据不足"
        elif row.get("correct") is True:
            status = "✅ 检测正确"
        elif row.get("correct") is False:
            status = "❌ 检测错误"
        else:
            status = "❓ 未知预期"

        detected = row.get("detected", "unknown")
        sentiment_map = {"bullish": "🟢利好", "bearish": "🔴利空", "neutral": "⚪中性", "unknown": "❓未知"}

        print(f"\n{'─' * 80}")
        print(f"日期: {row['date']}  |  类型: {row['event_type']}  |  {status}")
        print(f"事件: {row['title']}")
        print(f"预期: {row.get('expected', 'N/A')}  |  检测: {sentiment_map.get(detected, detected)}")

        if row.get("valid", False):
            print(f"  即时反应(4h): {row['immediate_change_pct']:+.2f}%  |  总变化: {row['total_change_pct']:+.2f}%")
            print(f"  量能比: {row['vol_ratio']:.2f}x  |  波动突增: {row['vol_spike']:.2f}x")
            print(f"  最大上冲: +{row['max_up_pct']:.2f}%  |  最大下探: {row['max_down_pct']:.2f}%")
            print(f"  综合评分: {row['sentiment_score']:+.4f}")

    # 汇总统计
    valid = df[df["valid"] & df["correct"].notna()]
    if len(valid) > 0:
        accuracy = valid["correct"].mean() * 100
        print(f"\n{'=' * 80}")
        print(f"📈 检测准确率: {accuracy:.1f}% ({int(valid['correct'].sum())}/{len(valid)})")

        # 按事件类型统计
        for etype in valid["event_type"].unique():
            sub = valid[valid["event_type"] == etype]
            acc = sub["correct"].mean() * 100
            print(f"  {etype}: {acc:.0f}% ({int(sub['correct'].sum())}/{len(sub)})")


# =========================================================================
# 3. 双向回测
# =========================================================================
def run_backtest_bilateral(
    price: pd.Series,
    params: dict | None = None,
    verbose: bool = False,
) -> dict:
    """运行双向回测（做多 + 做空）。

    Args:
        price: 收盘价序列
        params: 策略参数
        verbose: 是否打印交易日志

    Returns:
        回测指标字典
    """
    if params is None:
        params = {}

    strategy = TslaNewsEventStrategy()
    long_entries, long_exits, short_entries, short_exits = strategy.generate_signals_bilateral(price, **params)

    total_signals = long_entries.sum() + short_entries.sum()
    if total_signals == 0:
        return {"total_trades": 0, "total_return_pct": 0, "sharpe_ratio": 0}

    total_fee = OKX_SWAP.total_cost_per_trade

    portfolio = vbt.Portfolio.from_signals(
        close=price,
        entries=long_entries,
        exits=long_exits,
        short_entries=short_entries,
        short_exits=short_exits,
        init_cash=100_000.0,
        fees=total_fee,
        freq="1h",
    )

    metrics = compute_metrics(portfolio)

    # 附加交易日志
    trade_log = strategy.get_trade_log()
    if trade_log:
        metrics["n_long"] = sum(1 for t in trade_log if t["direction"] == "做多")
        metrics["n_short"] = sum(1 for t in trade_log if t["direction"] == "做空")
        metrics["avg_pnl"] = np.mean([t["pnl_pct"] for t in trade_log])

        if verbose:
            print("\n  --- 交易日志 ---")
            for t in trade_log:
                icon = "+" if t["pnl_pct"] > 0 else "-"
                print(
                    f"  [{icon}] {t['date']} | {t['direction']} | "
                    f"{t['title'][:30]} | "
                    f"入场 ${t['entry_price']:.2f} | "
                    f"P&L {t['pnl_pct']:+.2f}% | {t['exit_reason']}"
                )

    logger.info(
        f"双向回测完成 | 做多: {metrics.get('n_long', 0)} | "
        f"做空: {metrics.get('n_short', 0)} | "
        f"最终净值: {metrics.get('final_value', 0):,.2f}"
    )
    return metrics


def run_backtest(
    price: pd.Series,
    params: dict | None = None,
) -> dict:
    """向后兼容的单向回测（仅做多）。"""
    return run_backtest_bilateral(price, params)


def run_grid_search(price: pd.Series) -> pd.DataFrame:
    """参数网格搜索（双向交易）。

    Args:
        price: 收盘价序列

    Returns:
        结果 DataFrame
    """
    param_grid = {
        "reaction_hours": [2, 4, 6, 8],
        "hold_hours": [24, 48, 72, 96],
        "momentum_threshold": [0.3, 0.5, 1.0, 1.5],
        "stop_pct": [2.0, 3.0, 5.0],
        "take_profit_pct": [5.0, 8.0, 12.0, 15.0],
    }

    import itertools

    keys = list(param_grid.keys())
    combos = list(itertools.product(*param_grid.values()))
    logger.info(f"网格搜索 | 共 {len(combos)} 种组合")

    results = []
    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo, strict=True))
        try:
            metrics = run_backtest_bilateral(price, params)
            metrics.update(params)
            results.append(metrics)
        except Exception as e:
            logger.debug(f"组合 {i} 失败: {e}")
            continue

        if (i + 1) % 100 == 0:
            logger.info(f"进度: {i + 1}/{len(combos)}")

    results_df = pd.DataFrame(results)
    if results_df.empty:
        logger.warning("网格搜索无有效结果")
        return results_df

    # 按夏普排序
    results_df = results_df.sort_values("sharpe_ratio", ascending=False)
    return results_df


# =========================================================================
# 4. 事件窗口详细分析
# =========================================================================
def event_window_analysis(df: pd.DataFrame) -> None:
    """按事件类型分析收益特征。

    Args:
        df: OHLCV DataFrame
    """
    price = df["close"]
    df["volume"]

    print("\n" + "=" * 80)
    print("📊 事件窗口收益特征分析")
    print("=" * 80)

    # 按类型汇总
    type_stats: dict[str, list[float]] = {}

    for event in TSLA_NEWS_EVENTS:
        event_ts = pd.Timestamp(event.date, tz="UTC")
        post_mask = price.index >= event_ts
        if not post_mask.any():
            continue

        first_idx = post_mask.argmax()
        if first_idx + 48 >= len(price):
            continue

        entry_price = price.iloc[first_idx]

        # 计算多个窗口的收益（含前后半个月）
        for window_name, hours in [("4h", 4), ("12h", 12), ("24h", 24), ("48h", 48), ("7d", 168), ("15d", 360)]:
            end_idx = min(first_idx + hours, len(price) - 1)
            ret = (price.iloc[end_idx] - entry_price) / entry_price * 100

            key = f"{event.event_type}_{window_name}"
            if key not in type_stats:
                type_stats[key] = []
            type_stats[key].append(ret)

    # 打印
    for etype in ["earnings", "product", "ceo", "regulatory", "macro"]:
        print(f"\n  【{etype}】")
        for window in ["4h", "12h", "24h", "48h", "7d", "15d"]:
            key = f"{etype}_{window}"
            if key in type_stats and type_stats[key]:
                vals = type_stats[key]
                avg = np.mean(vals)
                med = np.median(vals)
                win_rate = sum(1 for v in vals if v > 0) / len(vals) * 100
                print(
                    f"    {window}: 平均 {avg:+.2f}% | 中位数 {med:+.2f}% | 胜率 {win_rate:.0f}% | 样本数 {len(vals)}"
                )


# =========================================================================
# 5. 主函数
# =========================================================================
async def main() -> None:
    """主入口。"""
    logger.info("=" * 60)
    logger.info("特斯拉新闻事件驱动量化分析")
    logger.info("=" * 60)

    # 加载数据
    df = await load_or_fetch_data(timeframe="1h")
    if df.empty:
        logger.error("无法获取数据，退出")
        return

    print(f"\n数据范围: {df.index[0]} ~ {df.index[-1]}")
    print(f"K线数量: {len(df)}")
    print(f"价格范围: ${df['close'].min():.2f} ~ ${df['close'].max():.2f}")
    print(f"待分析事件: {len(TSLA_NEWS_EVENTS)} 个")

    # --- Part 1: 利好/利空因子检测 ---
    logger.info("Part 1: 利好/利空因子检测")
    event_df = analyze_all_events(df["close"], df["volume"])
    print_event_analysis(event_df)

    # --- Part 2: 事件窗口收益分析 ---
    logger.info("Part 2: 事件窗口收益分析")
    event_window_analysis(df)

    # --- Part 3: 双向回测（做多 + 做空）---
    logger.info("Part 3: 双向回测（做多 + 做空）")
    price = df["close"]
    default_metrics = run_backtest_bilateral(price, verbose=True)
    print(f"\n{'=' * 80}")
    print("双向回测结果（默认参数）")
    print("=" * 80)
    for k, v in default_metrics.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")

    # --- Part 4: 参数优化 ---
    logger.info("Part 4: 参数网格搜索（这会花几分钟...）")
    grid_df = run_grid_search(price)

    if not grid_df.empty:
        print(f"\n{'=' * 80}")
        print("🏆 Top 10 参数组合（按夏普排序）")
        print("=" * 80)
        top10 = grid_df.head(10)
        display_cols = [
            "sharpe_ratio",
            "total_return_pct",
            "max_drawdown_pct",
            "win_rate_pct",
            "total_trades",
            "reaction_hours",
            "hold_hours",
            "momentum_threshold",
            "stop_pct",
            "take_profit_pct",
        ]
        existing_cols = [c for c in display_cols if c in top10.columns]
        print(top10[existing_cols].to_string(index=False))

        # 最优参数详情
        best = grid_df.iloc[0]
        print("\n🥇 最优参数:")
        print(f"  反应观察期: {int(best.get('reaction_hours', 4))}h")
        print(f"  最大持仓: {int(best.get('hold_hours', 48))}h")
        print(f"  动量阈值: {best.get('momentum_threshold', 0.5):.1f}%")
        print(f"  止损: {best.get('stop_pct', 3.0):.1f}%")
        print(f"  止盈: {best.get('take_profit_pct', 8.0):.1f}%")
        print(f"  夏普比: {best.get('sharpe_ratio', 0):.3f}")
        print(f"  总收益: {best.get('total_return_pct', 0):.2f}%")
        print(f"  最大回撤: {best.get('max_drawdown_pct', 0):.2f}%")
        print(f"  胜率: {best.get('win_rate_pct', 0):.1f}%")

    # 保存结果
    out_dir = Path("reports/tsla")
    out_dir.mkdir(parents=True, exist_ok=True)

    event_df.to_csv(out_dir / "event_sentiment_analysis.csv", index=False)
    if not grid_df.empty:
        grid_df.to_csv(out_dir / "grid_search_results.csv", index=False)

    logger.info(f"报告已保存到 {out_dir}/")
    logger.info("分析完成！")


if __name__ == "__main__":
    asyncio.run(main())
