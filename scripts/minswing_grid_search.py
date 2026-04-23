"""MinSwing 精细参数网格搜索。

在 ETH-USDT 和 NEAR-USDT 的 5m 数据上，用 OOS 后 1/3 数据做网格搜索。
使用 OKX_SWAP 费率，init_cash=250。
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings
from src.strategies.minute_swing import minute_swing_signal

COINS = ["ETH-USDT", "NEAR-USDT"]

GRID = {
    "trend_ma": [120, 150, 180, 210, 240],
    "stop_pct": [1.5, 2.0, 2.5, 3.0],
    "take_profit_pct": [5.0, 6.0, 8.0, 10.0, 12.0],
    "min_gap": [72, 96, 144, 192],
}


def load_price_5m(symbol: str) -> pd.Series | None:
    """加载 5m 收盘价。"""
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, "5m")
    if df.is_empty():
        logger.warning(f"{symbol} 5m 数据为空")
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


def main() -> None:
    import itertools

    total_combos = 1
    for v in GRID.values():
        total_combos *= len(v)

    print("=" * 110)
    print("MinSwing 精细参数网格搜索")
    print(f"费率: OKX_SWAP (taker={OKX_SWAP.taker_fee:.4%}, slippage={OKX_SWAP.slippage_bps}bp)")
    print(f"初始资金: 250 USDT")
    print(f"参数组合数: {total_combos}")
    print(f"搜索范围: {GRID}")
    print("=" * 110)

    for sym in COINS:
        print(f"\n{'='*110}")
        print(f"  {sym} - OOS 后 1/3 数据网格搜索")
        print(f"{'='*110}")

        price = load_price_5m(sym)
        if price is None or len(price) < 1000:
            print(f"  {sym} 数据不足，跳过")
            continue

        # OOS 后 1/3
        n = len(price)
        oos_start = n * 2 // 3
        oos_price = price.iloc[oos_start:]
        print(f"  总数据量: {n} 根 5m K线")
        print(f"  OOS 后 1/3: {len(oos_price)} 根 ({oos_price.index[0].strftime('%Y-%m-%d %H:%M')} ~ {oos_price.index[-1].strftime('%Y-%m-%d %H:%M')})")

        engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")

        print(f"  开始搜索 {total_combos} 个参数组合...\n")
        results_df, best_params = engine.run_grid_search(
            price=oos_price,
            signal_func=minute_swing_signal,
            param_grid=GRID,
        )

        # 按 sharpe_ratio 排序，取 top 10
        results_df_sorted = results_df.sort_values("sharpe_ratio", ascending=False).reset_index(drop=True)

        print(f"\n  === {sym} Top 10 结果 (按 Sharpe 排序) ===")
        header = (
            f"  {'Rank':<5} {'trend_ma':>8} {'stop%':>7} {'TP%':>7} {'min_gap':>8} "
            f"{'收益%':>9} {'Sharpe':>8} {'Sortino':>8} {'MaxDD%':>8} {'胜率%':>7} {'交易数':>6} {'终值':>10}"
        )
        print(header)
        print("  " + "-" * 106)

        top10 = results_df_sorted.head(10)
        for rank, (_, row) in enumerate(top10.iterrows(), 1):
            print(
                f"  {rank:<5} {int(row['trend_ma']):>8} {row['stop_pct']:>7.1f} {row['take_profit_pct']:>7.1f} {int(row['min_gap']):>8} "
                f"{row['total_return_pct']:>8.2f}% "
                f"{row['sharpe_ratio']:>8.3f} "
                f"{row['sortino_ratio']:>8.3f} "
                f"{row['max_drawdown_pct']:>7.2f}% "
                f"{row['win_rate_pct']:>6.1f}% "
                f"{int(row['total_trades']):>6} "
                f"{row['final_value']:>10.2f}"
            )

        print(f"\n  === {sym} 最优参数 ===")
        for k, v in best_params.items():
            print(f"    {k}: {v}")
        best_row = results_df_sorted.iloc[0]
        print(f"    => Sharpe: {best_row['sharpe_ratio']:.3f}, 收益: {best_row['total_return_pct']:.2f}%, MaxDD: {best_row['max_drawdown_pct']:.2f}%")

    print(f"\n{'='*110}")
    print("网格搜索完成。")


if __name__ == "__main__":
    main()
