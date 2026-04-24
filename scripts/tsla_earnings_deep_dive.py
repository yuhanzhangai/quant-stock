"""Q1 2026 财报深度事件窗口分析。

用 15m 数据精细分析 2026-04-22 Q1 财报发布前后的价格行为：
- 财报前 24h ~ 财报后 24h 的逐 15 分钟价格走势
- 量价关系、波动率变化
- 最佳入场/出场时机回溯
- 与历史财报事件对比
"""

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger
from src.strategies.tsla_news_event import detect_sentiment


def load_15m_data() -> pd.DataFrame:
    """加载 15 分钟 K 线数据。"""
    path = Path("data/parquet/ohlcv/swap/TSLA-USDT/15m.parquet")
    if not path.exists():
        logger.error(f"15m 数据不存在: {path}，请先运行 tsla_fetch_data.py")
        sys.exit(1)

    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True)
    return df


def load_1h_data() -> pd.DataFrame:
    """加载 1h K 线数据。"""
    path = Path("data/parquet/ohlcv/swap/TSLA-USDT/1h.parquet")
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True)
    return df


def analyze_earnings_window(
    df: pd.DataFrame,
    event_date: str,
    pre_hours: int = 24,
    post_hours: int = 24,
) -> None:
    """分析财报事件窗口。

    Args:
        df: K 线 DataFrame
        event_date: 事件日期
        pre_hours: 事件前小时数
        post_hours: 事件后小时数
    """
    event_ts = pd.Timestamp(event_date, tz="UTC")
    window_start = event_ts - pd.Timedelta(hours=pre_hours)
    window_end = event_ts + pd.Timedelta(hours=post_hours)

    window = df[window_start:window_end].copy()
    if window.empty:
        logger.error(f"事件窗口无数据: {event_date}")
        return

    # 基础信息
    print(f"\n{'=' * 80}")
    print(f"TSLA Q1 2026 财报深度分析 | 事件日期: {event_date}")
    print(f"分析窗口: {window.index[0]} ~ {window.index[-1]}")
    print(f"K线数量: {len(window)} (15m)")
    print(f"{'=' * 80}")

    # ---- 价格走势分段分析 ----
    pre_event = window[window.index < event_ts]
    post_event = window[window.index >= event_ts]

    if len(pre_event) > 0 and len(post_event) > 0:
        pre_close = pre_event["close"].iloc[-1]
        print("\n--- 价格概览 ---")
        print(f"  财报前收盘价: ${pre_close:.2f}")
        print(
            f"  财报后最高: ${post_event['high'].max():.2f} ({(post_event['high'].max() / pre_close - 1) * 100:+.2f}%)"
        )
        print(f"  财报后最低: ${post_event['low'].min():.2f} ({(post_event['low'].min() / pre_close - 1) * 100:+.2f}%)")
        print(
            f"  最终收盘: ${post_event['close'].iloc[-1]:.2f} ({(post_event['close'].iloc[-1] / pre_close - 1) * 100:+.2f}%)"
        )

    # ---- 逐时段分析 ----
    print("\n--- 分时段累计收益 ---")
    base_price = window["close"].iloc[0]
    time_points = [
        ("事件前 15d", event_ts - pd.Timedelta(days=15)),
        ("事件前 7d", event_ts - pd.Timedelta(days=7)),
        ("事件前 3d", event_ts - pd.Timedelta(days=3)),
        ("事件前 1d", event_ts - pd.Timedelta(days=1)),
        ("事件前 12h", event_ts - pd.Timedelta(hours=12)),
        ("事件前 6h", event_ts - pd.Timedelta(hours=6)),
        ("事件前 1h", event_ts - pd.Timedelta(hours=1)),
        ("事件时刻", event_ts),
        ("事件后 1h", event_ts + pd.Timedelta(hours=1)),
        ("事件后 4h", event_ts + pd.Timedelta(hours=4)),
        ("事件后 12h", event_ts + pd.Timedelta(hours=12)),
        ("事件后 1d", event_ts + pd.Timedelta(days=1)),
        ("事件后 3d", event_ts + pd.Timedelta(days=3)),
        ("事件后 7d", event_ts + pd.Timedelta(days=7)),
        ("事件后 15d", event_ts + pd.Timedelta(days=15)),
    ]

    event_price = None
    for label, ts in time_points:
        mask = window.index <= ts
        if not mask.any():
            continue
        closest_idx = window.index[mask][-1]
        price = window.loc[closest_idx, "close"]
        vol = window.loc[closest_idx, "volume"]
        ret_from_base = (price / base_price - 1) * 100

        if label == "事件时刻":
            event_price = price

        if event_price is not None and label != "事件时刻":
            ret_from_event = (price / event_price - 1) * 100
            print(
                f"  {label:>12}: ${price:.2f}  (vs窗口起: {ret_from_base:+.2f}%  vs事件: {ret_from_event:+.2f}%)  vol: {vol:.0f}"
            )
        else:
            print(f"  {label:>12}: ${price:.2f}  (vs窗口起: {ret_from_base:+.2f}%)  vol: {vol:.0f}")

    # ---- 量价分析 ----
    print("\n--- 量价分析 ---")
    if len(pre_event) > 0 and len(post_event) > 0:
        pre_avg_vol = pre_event["volume"].mean()
        post_avg_vol = post_event["volume"].mean()
        print(f"  事件前平均成交量: {pre_avg_vol:.0f}")
        print(f"  事件后平均成交量: {post_avg_vol:.0f}")
        print(f"  量能放大倍数: {post_avg_vol / max(pre_avg_vol, 1):.2f}x")

        # 找最大量能的时间
        max_vol_idx = window["volume"].idxmax()
        max_vol = window["volume"].max()
        print(f"  最大成交量时间: {max_vol_idx} ({max_vol:.0f})")

    # ---- 波动率分析 ----
    print("\n--- 波动率分析 ---")
    window["returns"] = window["close"].pct_change()

    if len(pre_event) > 1 and len(post_event) > 1:
        pre_vol_std = pre_event["close"].pct_change().std() * 100
        post_vol_std = post_event["close"].pct_change().std() * 100
        print(f"  事件前波动率(15m): {pre_vol_std:.4f}%")
        print(f"  事件后波动率(15m): {post_vol_std:.4f}%")
        print(f"  波动率变化: {post_vol_std / max(pre_vol_std, 0.0001):.2f}x")

    # ---- 最大连续涨跌 ----
    print("\n--- 连续涨跌 ---")
    returns = window["close"].pct_change().dropna()

    max_streak_up = 0
    max_streak_down = 0
    current_streak = 0

    for r in returns:
        if r > 0:
            if current_streak > 0:
                current_streak += 1
            else:
                current_streak = 1
            max_streak_up = max(max_streak_up, current_streak)
        elif r < 0:
            if current_streak < 0:
                current_streak -= 1
            else:
                current_streak = -1
            max_streak_down = max(max_streak_down, abs(current_streak))
        else:
            current_streak = 0

    print(f"  最大连续上涨: {max_streak_up} 根 K 线 ({max_streak_up * 15}分钟)")
    print(f"  最大连续下跌: {max_streak_down} 根 K 线 ({max_streak_down * 15}分钟)")

    # ---- 最佳交易时机 ----
    print("\n--- 最佳交易时机回溯 ---")
    if len(post_event) > 1:
        post_prices = post_event["close"]
        post_prices.iloc[0]

        # 做多最佳
        best_long_entry_idx = post_prices.idxmin()
        best_long_exit_idx = post_prices.loc[best_long_entry_idx:].idxmax()
        best_long_ret = (post_prices.loc[best_long_exit_idx] / post_prices.loc[best_long_entry_idx] - 1) * 100

        print("  做多最佳:")
        print(f"    入场: {best_long_entry_idx} @ ${post_prices.loc[best_long_entry_idx]:.2f}")
        print(f"    出场: {best_long_exit_idx} @ ${post_prices.loc[best_long_exit_idx]:.2f}")
        print(f"    收益: {best_long_ret:+.2f}%")

        # 做空最佳
        best_short_entry_idx = post_prices.idxmax()
        best_short_exit_after = post_prices.loc[best_short_entry_idx:]
        if len(best_short_exit_after) > 1:
            best_short_exit_idx = best_short_exit_after.idxmin()
            best_short_ret = (post_prices.loc[best_short_entry_idx] / post_prices.loc[best_short_exit_idx] - 1) * 100
            print("  做空最佳:")
            print(f"    入场: {best_short_entry_idx} @ ${post_prices.loc[best_short_entry_idx]:.2f}")
            print(f"    出场: {best_short_exit_idx} @ ${post_prices.loc[best_short_exit_idx]:.2f}")
            print(f"    收益: {best_short_ret:+.2f}%")

    # ---- 利好/利空因子检测 ----
    print("\n--- 利好/利空因子检测 ---")
    factors = detect_sentiment(
        window["close"],
        window["volume"],
        event_ts,
        pre_hours=pre_hours,
        post_hours=post_hours,
    )
    sentiment_map = {"bullish": "利好", "bearish": "利空", "neutral": "中性", "unknown": "未知"}
    print(f"  检测结果: {sentiment_map.get(factors.get('detected', 'unknown'), '未知')}")
    print(f"  综合评分: {factors.get('sentiment_score', 0):+.4f}")
    print(f"  即时反应(4h): {factors.get('immediate_change_pct', 0):+.2f}%")
    print(f"  总变化: {factors.get('total_change_pct', 0):+.2f}%")
    print(f"  量能比: {factors.get('vol_ratio', 0):.2f}x")
    print(f"  波动突增: {factors.get('vol_spike', 0):.2f}x")


def compare_all_events(df_1h: pd.DataFrame) -> None:
    """对比所有可用的财报/销量类事件。"""
    from src.strategies.tsla_news_event import TSLA_NEWS_EVENTS

    earnings_events = [e for e in TSLA_NEWS_EVENTS if e.event_type == "earnings"]

    print(f"\n{'=' * 80}")
    print("所有财报/销量事件对比")
    print(f"{'=' * 80}")
    print(f"{'日期':>12} | {'事件':>30} | {'4h变化':>8} | {'24h变化':>8} | {'48h变化':>8} | {'量能比':>6}")
    print("-" * 90)

    for event in earnings_events:
        event_ts = pd.Timestamp(event.date, tz="UTC")
        post_mask = df_1h.index >= event_ts
        if not post_mask.any():
            continue

        first_idx = post_mask.argmax()
        entry_price = df_1h["close"].iloc[first_idx]
        pre_start = event_ts - pd.Timedelta(hours=24)
        pre_vol = df_1h[pre_start:event_ts]["volume"].mean()
        post_vol = df_1h[event_ts : event_ts + pd.Timedelta(hours=24)]["volume"].mean()
        vol_ratio = post_vol / max(pre_vol, 1)

        changes = {}
        for label, hours in [("4h", 4), ("24h", 24), ("48h", 48)]:
            end_idx = min(first_idx + hours, len(df_1h) - 1)
            changes[label] = (df_1h["close"].iloc[end_idx] / entry_price - 1) * 100

        title_short = event.title[:28]
        print(
            f"  {event.date} | {title_short:>30} | "
            f"{changes.get('4h', 0):+.2f}% | "
            f"{changes.get('24h', 0):+.2f}% | "
            f"{changes.get('48h', 0):+.2f}% | "
            f"{vol_ratio:.2f}x"
        )


def suggest_strategy(df_15m: pd.DataFrame, event_date: str) -> None:
    """基于分析给出策略建议。"""
    event_ts = pd.Timestamp(event_date, tz="UTC")
    post = df_15m[df_15m.index >= event_ts]

    if len(post) < 8:
        return

    # 分析前几根 K 线的方向
    first_4_bars = post.iloc[:4]  # 前 1 小时
    first_change = (first_4_bars["close"].iloc[-1] / first_4_bars["close"].iloc[0] - 1) * 100

    first_16_bars = post.iloc[:16]  # 前 4 小时
    four_h_change = (first_16_bars["close"].iloc[-1] / first_16_bars["close"].iloc[0] - 1) * 100

    print(f"\n{'=' * 80}")
    print("策略建议")
    print(f"{'=' * 80}")

    print(f"  财报后 1h 方向: {'+' if first_change > 0 else ''}{first_change:.2f}%")
    print(f"  财报后 4h 方向: {'+' if four_h_change > 0 else ''}{four_h_change:.2f}%")

    if abs(first_change) < 0.3:
        print("\n  [建议] 即时反应弱，市场仍在消化，不宜急于入场")
        print("  等待 4h 后确认方向再操作")
    elif first_change > 0.5:
        print("\n  [建议] 即时反应偏多，可考虑:")
        print("  1. 突破追多: 突破前高后入场，止损设在事件价格下方")
        print("  2. 回调做多: 等待回调至事件价格附近再入场")
        print(f"  止损建议: {abs(first_change) * 0.5:.1f}% ~ {abs(first_change):.1f}%")
    else:
        print("\n  [建议] 即时反应偏空，可考虑:")
        print("  1. 跟空: 跌破前低后入场做空，止损设在事件价格上方")
        print("  2. 反弹做空: 等待反弹至事件价格附近再做空")
        print(f"  止损建议: {abs(first_change) * 0.5:.1f}% ~ {abs(first_change):.1f}%")

    # 波动率提示
    pre_4h = df_15m[event_ts - pd.Timedelta(hours=4) : event_ts]
    if len(pre_4h) > 1 and len(first_16_bars) > 1:
        pre_std = pre_4h["close"].pct_change().std()
        post_std = first_16_bars["close"].pct_change().std()
        if post_std > pre_std * 2:
            print(f"\n  !! 波动率放大 {post_std / max(pre_std, 1e-10):.1f}x，注意仓位控制")


def main() -> None:
    """主入口。"""
    logger.info("Q1 2026 财报深度分析")

    # 加载数据
    df_15m = load_15m_data()
    df_1h = load_1h_data()

    # Q1 2026 财报 = 2026-04-22
    event_date = "2026-04-22"

    # 15m 精细分析 — 前后半个月 (360h)
    analyze_earnings_window(df_15m, event_date, pre_hours=360, post_hours=360)

    # 所有财报事件对比
    compare_all_events(df_1h)

    # 策略建议
    suggest_strategy(df_15m, event_date)

    # 尝试获取最新新闻
    print(f"\n{'=' * 80}")
    print("最新 TSLA 新闻情绪")
    print(f"{'=' * 80}")
    try:
        from src.news.tsla_news_fetcher import (
            fetch_google_news_rss,
            get_sentiment_summary,
            save_news_cache,
        )

        news = fetch_google_news_rss("Tesla TSLA earnings Q1 2026", max_items=20)
        if news:
            for item in news[:10]:
                sentiment_icon = {"bullish": "[+]", "bearish": "[-]", "neutral": "[=]"}.get(item.sentiment, "[?]")
                print(f"  {sentiment_icon} {item.title[:75]}")
                if item.keywords_matched:
                    print(f"       keywords: {', '.join(item.keywords_matched[:5])}")

            summary = get_sentiment_summary(news)
            print(f"\n  整体情绪: {summary['overall']} (avg score: {summary['avg_score']:+.3f})")
            print(f"  利好/利空比: {summary['bullish_count']}/{summary['bearish_count']}")

            save_news_cache(news)
        else:
            print("  (未能获取到新闻，可能需要网络代理)")
    except Exception as e:
        print(f"  新闻获取失败: {e}")
        print("  (这不影响历史数据分析，新闻功能需要能访问 Google)")


if __name__ == "__main__":
    main()
