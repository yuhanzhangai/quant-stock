"""分钟线 Donchian 突破策略 3 段验证。

在 ETH/BTC/SOL 5m 数据上分 3 段回测，检验策略在不同市场环境下的稳健性。
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings

from src.strategies.minute_breakout import minute_breakout_signal

COINS = ["ETH-USDT", "BTC-USDT", "SOL-USDT"]
PARAMS = {
    "donchian_period": 120,
    "atr_period": 20,
    "atr_stop_mult": 2.0,
    "trend_ma": 300,
    "min_gap": 72,
    "take_profit_pct": 3.0,
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
    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="5min")

    print("=" * 100)
    print("分钟线 Donchian 突破策略 3 段验证 (5m)")
    print(f"参数: {PARAMS}")
    print("=" * 100)

    header = f"{'币种':<12} {'段':<8} {'时间范围':<35} {'收益%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'胜率%':>8} {'交易数':>6}"
    print(header)
    print("-" * 100)

    all_rows = []

    for sym in COINS:
        price = load_price_5m(sym)
        if price is None or len(price) < 1000:
            print(f"{sym:<12} 数据不足，跳过")
            continue

        # 3 段等分
        n = len(price)
        seg_size = n // 3
        segments = [
            ("段1", price.iloc[:seg_size]),
            ("段2", price.iloc[seg_size: seg_size * 2]),
            ("段3", price.iloc[seg_size * 2:]),
        ]

        for seg_name, seg_price in segments:
            if len(seg_price) < 500:
                print(f"{sym:<12} {seg_name:<8} 数据不足 ({len(seg_price)} 根)")
                continue

            period_str = f"{seg_price.index[0].strftime('%Y-%m-%d %H:%M')} ~ {seg_price.index[-1].strftime('%Y-%m-%d %H:%M')}"

            try:
                entries, exits = minute_breakout_signal(seg_price, **PARAMS)
                pf = engine.run(seg_price, entries, exits)
                m = compute_metrics(pf)
            except Exception as e:
                logger.error(f"{sym} {seg_name} 回测失败: {e}")
                m = {
                    "total_return_pct": 0, "sharpe_ratio": 0,
                    "max_drawdown_pct": 0, "win_rate_pct": 0, "total_trades": 0,
                }

            row = {
                "coin": sym,
                "segment": seg_name,
                "period": period_str,
                **m,
            }
            all_rows.append(row)

            print(
                f"{sym:<12} {seg_name:<8} {period_str:<35} "
                f"{m['total_return_pct']:>7.2f}% "
                f"{m['sharpe_ratio']:>8.2f} "
                f"{m['max_drawdown_pct']:>7.2f}% "
                f"{m['win_rate_pct']:>7.1f}% "
                f"{m['total_trades']:>6d}"
            )

    print("=" * 100)

    # 汇总统计
    if all_rows:
        df = pd.DataFrame(all_rows)
        print("\n汇总:")
        print(f"  平均收益:  {df['total_return_pct'].mean():.2f}%")
        print(f"  平均 Sharpe: {df['sharpe_ratio'].mean():.2f}")
        print(f"  平均 MaxDD:  {df['max_drawdown_pct'].mean():.2f}%")
        print(f"  平均胜率:  {df['win_rate_pct'].mean():.1f}%")
        print(f"  总交易数:  {df['total_trades'].sum()}")
        profitable = (df["total_return_pct"] > 0).sum()
        print(f"  盈利段数:  {profitable}/{len(df)}")


if __name__ == "__main__":
    main()
