"""做空策略鲁棒性验证。

三重验证：
1. 样本外验证：用 4h 数据（2024-2026两年）测试，与 5m 结果对比
2. 牛市纪律测试：专门找牛市时段，检查策略是否"管住手"
3. 极端行情测试：大涨大跌月份的表现

目标：确认策略不是过拟合，在牛市中不会乱开空单亏钱。
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings
from src.strategies.short_swing import ShortSwingStrategy, invert_price
from src.strategies.short_trend_follow import ShortTrendFollowStrategy
from src.strategies.short_swing_trail import ShortSwingTrailStrategy
from src.strategies.short_rsi_overbought import ShortRSIOverboughtStrategy


COINS_5M = ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT",
            "DOT-USDT", "OP-USDT", "SUI-USDT", "ATOM-USDT", "PEPE-USDT", "FIL-USDT"]
COINS_4H = ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT",
            "DOT-USDT", "OP-USDT", "SUI-USDT", "PEPE-USDT", "FIL-USDT"]

# 冠军参数（从迭代中确认的最优）
STRATEGIES = {
    "trend_follow": (
        ShortTrendFollowStrategy(),
        {"fast_ma": 96, "slow_ma": 180, "min_gap": 336, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
    ),
    "swing_trail": (
        ShortSwingTrailStrategy(),
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.5, "trail_pct": 1.5, "min_profit": 2.0},
    ),
    "short_swing": (
        ShortSwingStrategy(),
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.0, "take_profit_pct": 6.0},
    ),
    "rsi_overbought": (
        ShortRSIOverboughtStrategy(),
        {"trend_ma": 180, "rsi_overbought": 65, "rsi_entry_cross": 55, "min_gap": 192, "stop_pct": 2.0, "take_profit_pct": 8.0},
    ),
}


def load_price(symbol: str, tf: str) -> pd.Series | None:
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, tf)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


def classify_market(price: pd.Series, ma_period: int = 50) -> str:
    """简单分类市场状态：BULL / BEAR / SIDEWAYS。"""
    if len(price) < ma_period + 10:
        return "UNKNOWN"
    ma = price.rolling(window=ma_period).mean()
    # 最后 20% 的数据判断趋势
    tail = int(len(price) * 0.8)
    price_tail = price.iloc[tail:]
    ma_tail = ma.iloc[tail:]

    pct_above = (price_tail > ma_tail).mean()
    total_return = (price.iloc[-1] - price.iloc[0]) / price.iloc[0] * 100

    if total_return > 15 and pct_above > 0.6:
        return "BULL"
    elif total_return < -15 and pct_above < 0.4:
        return "BEAR"
    else:
        return "SIDEWAYS"


def find_bull_bear_periods(price: pd.Series, window: int = 50) -> dict:
    """按月份划分牛市/熊市/震荡。"""
    monthly = price.resample("ME").agg(["first", "last"])
    monthly["return_pct"] = (monthly["last"] - monthly["first"]) / monthly["first"] * 100

    bull_months = monthly[monthly["return_pct"] > 10].index
    bear_months = monthly[monthly["return_pct"] < -10].index
    sideways_months = monthly[(monthly["return_pct"] >= -10) & (monthly["return_pct"] <= 10)].index

    return {
        "bull": bull_months,
        "bear": bear_months,
        "sideways": sideways_months,
        "monthly_returns": monthly["return_pct"],
    }


def test_on_period(
    strat,
    params: dict,
    price: pd.Series,
    engine: BacktestEngine,
) -> dict:
    """在指定时间段上测试策略。"""
    if len(price) < 100:
        return {"trades": 0, "return_pct": 0.0, "sharpe": 0.0, "final_value": engine._init_cash}

    try:
        entries, exits = strat.generate_signals(price, **params)
    except Exception:
        return {"trades": 0, "return_pct": 0.0, "sharpe": 0.0, "final_value": engine._init_cash}

    n_entries = int(entries.sum())
    if n_entries == 0:
        return {"trades": 0, "return_pct": 0.0, "sharpe": 0.0, "final_value": engine._init_cash}

    price_inv = invert_price(price)
    pf = engine.run(price_inv, entries, exits)
    m = compute_metrics(pf)

    return {
        "trades": n_entries,
        "return_pct": m["total_return_pct"],
        "sharpe": m["sharpe_ratio"],
        "final_value": m["final_value"],
        "win_rate": m["win_rate_pct"],
        "max_dd": m["max_drawdown_pct"],
    }


def main() -> None:
    engine_5m = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")
    engine_4h = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="4h")

    # ================================================================
    # TEST 1: 4h 数据样本外验证（2024-2026）
    # ================================================================
    print("=" * 100)
    print("  TEST 1: 样本外验证 — 4h 数据（2024-2026两年）")
    print("  策略在 5m 数据上优化，现在放到完全不同周期的 4h 数据上检验")
    print("  注意：4h 数据的 MA180 = 180*4h = 720h = 30天（与5m的15h完全不同）")
    print("=" * 100)

    # 4h 需要调整 MA 参数（MA周期代表的含义不同）
    # 5m MA180 = 15h，等效 4h 的 MA = 15h/4h = ~4
    # 但为了公平测试，我们用原始参数看策略在不同时间尺度下的泛化能力
    # 4h 数据分半年段测试，避免 invert_price 在长序列上价格变负
    print("\n  用原始参数直接跑 4h 数据（按半年分段，避免价格反转溢出）:")
    print(f"  {'策略':<18} | {'币种':<6} | {'时段':>12} | {'市场':>6} | {'交易':>4} | {'收益%':>8} | {'夏普':>6} | {'胜率%':>6}")
    print(f"  {'─' * 90}")

    for coin in COINS_4H:
        price_4h = load_price(coin, "4h")
        if price_4h is None or len(price_4h) < 200:
            continue

        coin_short = coin.replace("-USDT", "")

        # 按半年分段
        segments_4h = []
        for year in [2024, 2025, 2026]:
            for half in [("H1", f"{year}-01", f"{year}-06"), ("H2", f"{year}-07", f"{year}-12")]:
                label, start, end = half
                mask = (price_4h.index >= start) & (price_4h.index < f"{int(end[:4]) + (1 if end[5:] == '12' else 0)}-{'01' if end[5:] == '12' else str(int(end[5:7])+1).zfill(2)}")
                seg = price_4h[mask]
                if len(seg) >= 100:
                    segments_4h.append((f"{year}{label}", seg))

        for seg_label, seg_price in segments_4h:
            market = classify_market(seg_price, ma_period=30)

            for strat_name, (strat, params) in STRATEGIES.items():
                r = test_on_period(strat, params, seg_price, engine_4h)
                trades = r["trades"]
                ret = r["return_pct"]
                sharpe = r["sharpe"]
                wr = r.get("win_rate", 0)

                flag = ""
                if market == "BULL" and trades > 3 and ret < -10:
                    flag = " !! 牛市大亏"
                elif market == "BULL" and trades == 0:
                    flag = " OK 不交易"
                elif market == "BEAR" and ret > 5:
                    flag = " GOOD"

                if trades > 0 or market == "BULL":  # 只显示有交易的或牛市的
                    print(
                        f"  {strat_name:<18} | {coin_short:<6} | {seg_label:>12} | {market:>6} | {trades:>4} | "
                        f"{ret:>+8.2f} | {sharpe:>6.2f} | {wr:>6.1f}{flag}"
                    )

    # ================================================================
    # TEST 2: 牛市纪律测试（5m 数据按月份切片）
    # ================================================================
    print(f"\n\n{'=' * 100}")
    print("  TEST 2: 牛市纪律测试 — 策略在上涨月份的表现")
    print("  好的做空策略应该在牛市中：A) 不交易  B) 少交易  C) 至少不大亏")
    print("=" * 100)

    for coin in ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"]:
        price_5m = load_price(coin, "5m")
        if price_5m is None:
            continue

        coin_short = coin.replace("-USDT", "")
        periods = find_bull_bear_periods(price_5m)

        print(f"\n  {coin_short} — 月度收益分布:")
        for month, ret in periods["monthly_returns"].items():
            label = "BULL" if ret > 10 else ("BEAR" if ret < -10 else "SIDE")
            bar = "+" * int(abs(ret) / 2) if ret > 0 else "-" * int(abs(ret) / 2)
            print(f"    {month.strftime('%Y-%m')} | {ret:>+7.1f}% | {label:>4} | {bar}")

        # 在牛市月份测试
        bull_months = periods["bull"]
        if len(bull_months) == 0:
            print(f"    没有牛市月份（涨幅>10%）")
            continue

        print(f"\n    牛市月份做空测试:")
        print(f"    {'策略':<18} | {'月份':>8} | {'月涨幅%':>8} | {'交易':>4} | {'做空收益%':>10} | 判定")
        print(f"    {'─' * 70}")

        for month in bull_months:
            # 提取该月份的 5m 数据
            month_start = month.replace(day=1)
            month_end = month
            month_mask = (price_5m.index >= str(month_start)) & (price_5m.index <= str(month_end + pd.Timedelta(days=1)))
            month_price = price_5m[month_mask]

            if len(month_price) < 100:
                continue

            month_ret = periods["monthly_returns"][month]

            for strat_name, (strat, params) in STRATEGIES.items():
                r = test_on_period(strat, params, month_price, engine_5m)
                trades = r["trades"]
                ret = r["return_pct"]

                if trades == 0:
                    verdict = "PASS (不交易)"
                elif ret >= 0:
                    verdict = "PASS (盈利或持平)"
                elif ret > -3:
                    verdict = "OK (小亏可接受)"
                elif ret > -10:
                    verdict = "WARN (亏损较大)"
                else:
                    verdict = "FAIL (大亏!)"

                print(
                    f"    {strat_name:<18} | {month.strftime('%Y-%m'):>8} | "
                    f"{month_ret:>+8.1f} | {trades:>4} | {ret:>+10.2f} | {verdict}"
                )

    # ================================================================
    # TEST 3: 熊市表现确认（最大下跌月份）
    # ================================================================
    print(f"\n\n{'=' * 100}")
    print("  TEST 3: 熊市确认 — 策略在下跌月份应该赚钱")
    print("=" * 100)

    for coin in ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"]:
        price_5m = load_price(coin, "5m")
        if price_5m is None:
            continue

        coin_short = coin.replace("-USDT", "")
        periods = find_bull_bear_periods(price_5m)
        bear_months = periods["bear"]

        if len(bear_months) == 0:
            print(f"\n  {coin_short}: 没有熊市月份（跌幅>10%）")
            continue

        print(f"\n  {coin_short} 熊市月份做空表现:")
        print(f"    {'策略':<18} | {'月份':>8} | {'月跌幅%':>8} | {'交易':>4} | {'做空收益%':>10} | {'夏普':>6}")
        print(f"    {'─' * 70}")

        for month in bear_months:
            month_start = month.replace(day=1)
            month_end = month
            month_mask = (price_5m.index >= str(month_start)) & (price_5m.index <= str(month_end + pd.Timedelta(days=1)))
            month_price = price_5m[month_mask]

            if len(month_price) < 100:
                continue

            month_ret = periods["monthly_returns"][month]

            for strat_name, (strat, params) in STRATEGIES.items():
                r = test_on_period(strat, params, month_price, engine_5m)
                trades = r["trades"]
                ret = r["return_pct"]
                sharpe = r["sharpe"]

                print(
                    f"    {strat_name:<18} | {month.strftime('%Y-%m'):>8} | "
                    f"{month_ret:>+8.1f} | {trades:>4} | {ret:>+10.2f} | {sharpe:>6.2f}"
                )

    # ================================================================
    # 总结
    # ================================================================
    print(f"\n\n{'=' * 100}")
    print("  鲁棒性验证总结")
    print("=" * 100)
    print("""
  检查清单:
  [1] 样本外泛化: 策略在 4h 数据上是否还有效？
      - 如果 4h 表现与 5m 一致 → 策略逻辑稳健
      - 如果 4h 表现崩溃 → 可能过拟合 5m 数据的噪音

  [2] 牛市纪律: 策略在上涨月份是否管住了手？
      - PASS (不交易) → 完美纪律
      - OK (小亏) → 可接受，趋势过滤在工作
      - FAIL (大亏) → 需要加强趋势过滤

  [3] 熊市确认: 策略在下跌月份是否真的赚钱？
      - 有交易且盈利 → 策略有效
      - 有交易但亏损 → 信号质量有问题
      - 无交易 → 太保守，需要降低阈值
    """)


if __name__ == "__main__":
    main()
