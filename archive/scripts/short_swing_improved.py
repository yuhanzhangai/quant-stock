"""做空策略改进 + 多空组合验证。

1. short_swing 参数网格：min_gap=(144,192,288), rsi_entry=(55,60,65)
2. long_short_combo: 上升趋势做多(MinSwing) + 下降趋势做空(ShortSwing)
3. ETH/SOL 5m 数据 3 段验证
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from src.strategies.minute_swing import MinuteSwingStrategy
from src.strategies.short_swing import ShortSwingStrategy, invert_price

COINS = ["ETH-USDT", "SOL-USDT"]


def load_price(symbol: str, tf: str) -> pd.Series | None:
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, tf)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


def split_3_segments(price: pd.Series) -> list[tuple[str, pd.Series]]:
    n = len(price)
    seg_len = n // 3
    segments = []
    for i, label in enumerate(["段1(前)", "段2(中)", "段3(后)"]):
        start = i * seg_len
        end = (i + 1) * seg_len if i < 2 else n
        segments.append((label, price.iloc[start:end]))
    return segments


def long_short_combo_signal(
    price: pd.Series,
    # MinSwing (做多) 参数
    long_trend_ma: int = 240,
    long_min_gap: int = 72,
    long_stop_pct: float = 2.0,
    long_tp_pct: float = 4.0,
    # ShortSwing (做空) 参数
    short_trend_ma: int = 180,
    short_min_gap: int = 192,
    short_rsi_entry: int = 60,
    short_stop_pct: float = 2.0,
    short_tp_pct: float = 8.0,
    **kwargs,
) -> tuple[pd.Series, pd.Series]:
    """多空组合信号。

    上升趋势 -> MinuteSwing 做多信号 (直接使用原始价格)
    下降趋势 -> ShortSwing 做空信号 (需要反转价格回测)

    由于 vectorbt 只支持做多，我们用一个统一的反转策略:
    - 做多信号直接在原始价格上回测
    - 做空信号在反转价格上回测
    - 将两者的 PnL 合并

    但为了简化，我们这里用一个巧妙的方法:
    将做多和做空区间分开，做多区间用原始价格，做空区间用反转价格，
    拼接成一个合成价格序列，然后统一回测。

    更简单的方案: 分别回测做多和做空，然后合并结果。
    """
    long_strat = MinuteSwingStrategy()
    short_strat = ShortSwingStrategy()

    # 做多信号 (原始价格)
    long_entries, long_exits = long_strat.generate_signals(
        price,
        trend_ma=long_trend_ma,
        min_gap=long_min_gap,
        stop_pct=long_stop_pct,
        take_profit_pct=long_tp_pct,
    )

    # 做空信号 (原始价格上生成)
    short_entries, short_exits = short_strat.generate_signals(
        price,
        trend_ma=short_trend_ma,
        min_gap=short_min_gap,
        rsi_entry=short_rsi_entry,
        stop_pct=short_stop_pct,
        take_profit_pct=short_tp_pct,
    )

    return long_entries, long_exits, short_entries, short_exits


def run_combo_backtest(price: pd.Series, engine: BacktestEngine) -> dict:
    """分别回测做多和做空，合并 PnL。"""
    long_entries, long_exits, short_entries, short_exits = long_short_combo_signal(price)

    n_long = int(long_entries.sum())
    n_short = int(short_entries.sum())

    result = {
        "long_trades": n_long,
        "short_trades": n_short,
        "total_trades": n_long + n_short,
    }

    init_cash = engine._init_cash

    # 做多部分: 分配一半资金
    long_engine = BacktestEngine(costs=engine._costs, init_cash=init_cash / 2, freq=engine._freq)
    short_engine = BacktestEngine(costs=engine._costs, init_cash=init_cash / 2, freq=engine._freq)

    long_final = init_cash / 2
    short_final = init_cash / 2
    long_ret = 0.0
    short_ret = 0.0
    long_sharpe = 0.0
    short_sharpe = 0.0
    long_dd = 0.0
    short_dd = 0.0

    if n_long > 0:
        pf_long = long_engine.run(price, long_entries, long_exits)
        m_long = compute_metrics(pf_long)
        long_final = m_long["final_value"]
        long_ret = m_long["total_return_pct"]
        long_sharpe = m_long["sharpe_ratio"]
        long_dd = m_long["max_drawdown_pct"]

    if n_short > 0:
        price_inv = invert_price(price)
        pf_short = short_engine.run(price_inv, short_entries, short_exits)
        m_short = compute_metrics(pf_short)
        short_final = m_short["final_value"]
        short_ret = m_short["total_return_pct"]
        short_sharpe = m_short["sharpe_ratio"]
        short_dd = m_short["max_drawdown_pct"]

    combo_final = long_final + short_final
    combo_ret = (combo_final - init_cash) / init_cash * 100

    result.update(
        {
            "long_ret_pct": long_ret,
            "short_ret_pct": short_ret,
            "long_final": long_final,
            "short_final": short_final,
            "combo_final": combo_final,
            "combo_ret_pct": combo_ret,
            "long_sharpe": long_sharpe,
            "short_sharpe": short_sharpe,
            "long_dd": long_dd,
            "short_dd": short_dd,
        }
    )
    return result


def run_long_only_backtest(price: pd.Series, engine: BacktestEngine) -> dict:
    """纯做多基准。"""
    strat = MinuteSwingStrategy()
    entries, exits = strat.generate_signals(price)
    n_entries = int(entries.sum())
    if n_entries == 0:
        return {
            "total_return_pct": 0.0,
            "final_value": engine._init_cash,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "total_trades": 0,
        }
    pf = engine.run(price, entries, exits)
    return compute_metrics(pf)


def main() -> None:
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")

    # ============================================================
    # Part 1: ShortSwing 参数网格搜索
    # ============================================================
    print("=" * 90)
    print("  Part 1: ShortSwing 参数改进 — min_gap x rsi_entry 网格")
    print("  费率: OKX_SWAP | 初始资金: $250 | 反转价格模拟做空")
    print("=" * 90)

    short_strat = ShortSwingStrategy()
    param_combos = []
    for mg in [144, 192, 288]:
        for rsi in [55, 60, 65]:
            param_combos.append((mg, rsi))

    for coin in COINS:
        price = load_price(coin, "5m")
        if price is None or len(price) < 500:
            print(f"\n{coin}: 数据不足，跳过")
            continue

        print(f"\n{'─' * 85}")
        print(f"  {coin}  |  总数据量: {len(price)} 根 5m K线")
        print(f"  时间范围: {price.index[0]} ~ {price.index[-1]}")
        print(f"{'─' * 85}")
        print(
            f"  {'min_gap':>8} | {'rsi':>4} | {'段':>6} | {'入场':>4} | "
            f"{'总收益%':>8} | {'夏普':>6} | {'最大回撤%':>9} | "
            f"{'胜率%':>6} | {'终值$':>8}"
        )
        print(f"  {'─' * 80}")

        segments = split_3_segments(price)

        for mg, rsi in param_combos:
            for seg_label, seg_price in segments:
                if len(seg_price) < 300:
                    print(f"  {mg:>8} | {rsi:>4} | {seg_label:>6} | 数据不足")
                    continue

                entries, exits = short_strat.generate_signals(seg_price, min_gap=mg, rsi_entry=rsi)
                n_entries = int(entries.sum())

                if n_entries == 0:
                    print(
                        f"  {mg:>8} | {rsi:>4} | {seg_label:>6} | {n_entries:>4} | "
                        f"{'无交易':>8} |    --- |       --- |    --- |      ---"
                    )
                    continue

                price_inv = invert_price(seg_price)
                pf = engine.run(price_inv, entries, exits)
                m = compute_metrics(pf)

                print(
                    f"  {mg:>8} | {rsi:>4} | {seg_label:>6} | {n_entries:>4} | "
                    f"{m['total_return_pct']:>+8.2f} | {m['sharpe_ratio']:>6.2f} | "
                    f"{m['max_drawdown_pct']:>9.2f} | {m['win_rate_pct']:>6.1f} | "
                    f"{m['final_value']:>8.2f}"
                )

    # ============================================================
    # Part 2 & 3: 多空组合 vs 纯做多 — 三段验证
    # ============================================================
    print(f"\n\n{'=' * 90}")
    print("  Part 2: 多空组合 (Long+Short Combo) vs 纯做多 (Long Only)")
    print("  上升趋势 -> MinSwing做多 | 下降趋势 -> ShortSwing做空")
    print("  各分配50%资金 | 费率: OKX_SWAP | 初始资金: $250")
    print("=" * 90)

    for coin in COINS:
        price = load_price(coin, "5m")
        if price is None or len(price) < 500:
            print(f"\n{coin}: 数据不足，跳过")
            continue

        print(f"\n{'─' * 85}")
        print(f"  {coin}  |  总数据量: {len(price)} 根 5m K线")
        print(f"  时间范围: {price.index[0]} ~ {price.index[-1]}")
        print(f"{'─' * 85}")

        segments = split_3_segments(price)

        print(
            f"  {'段':>6} | {'模式':>10} | {'做多笔':>6} | {'做空笔':>6} | {'收益%':>8} | {'终值$':>8} | {'备注':>20}"
        )
        print(f"  {'─' * 80}")

        for seg_label, seg_price in segments:
            if len(seg_price) < 300:
                print(f"  {seg_label:>6} | 数据不足 ({len(seg_price)} 根)")
                continue

            # --- 纯做多 ---
            long_only = run_long_only_backtest(seg_price, engine)
            lo_trades = long_only.get("total_trades", 0)
            lo_ret = long_only.get("total_return_pct", 0.0)
            lo_final = long_only.get("final_value", 250.0)

            print(
                f"  {seg_label:>6} | {'纯做多':>10} | {lo_trades:>6} | {'--':>6} | "
                f"{lo_ret:>+8.2f} | {lo_final:>8.2f} | "
                f"Sharpe={long_only.get('sharpe_ratio', 0):.2f}"
            )

            # --- 多空组合 ---
            combo = run_combo_backtest(seg_price, engine)

            print(
                f"  {seg_label:>6} | {'多空组合':>10} | "
                f"{combo['long_trades']:>6} | {combo['short_trades']:>6} | "
                f"{combo['combo_ret_pct']:>+8.2f} | {combo['combo_final']:>8.2f} | "
                f"多:{combo['long_ret_pct']:+.1f}% 空:{combo['short_ret_pct']:+.1f}%"
            )

            # 比较
            diff = combo["combo_ret_pct"] - lo_ret
            winner = "多空组合胜" if diff > 0 else "纯做多胜"
            print(f"  {seg_label:>6} | {'>>> 对比':>10} | {'':>6} | {'':>6} | {diff:>+8.2f} | {'':>8} | {winner}")
            print(f"  {'':>6} |{'':>11}|{'':>7}|{'':>7}|{'':>9}|{'':>9}|")

    print(f"\n{'=' * 90}")
    print("  验证完成")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    main()
