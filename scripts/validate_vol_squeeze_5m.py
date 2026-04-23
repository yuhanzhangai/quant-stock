"""波动率收缩爆发策略 (5m) 三段验证。

对 ETH/BTC/SOL 5m 数据分成 3 段做回测验证。
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings
from src.strategies.vol_squeeze_5m import vol_squeeze_5m_signal


COINS = ["ETH-USDT", "BTC-USDT", "SOL-USDT"]


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
    """将价格序列均分 3 段。"""
    n = len(price)
    seg_len = n // 3
    segments = []
    for i, label in enumerate(["段1(前)", "段2(中)", "段3(后)"]):
        start = i * seg_len
        end = (i + 1) * seg_len if i < 2 else n
        seg = price.iloc[start:end]
        segments.append((label, seg))
    return segments


def main() -> None:
    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="5min")

    print("=" * 80)
    print("  波动率收缩爆发策略 (vol_squeeze_5m) — 三段验证")
    print("=" * 80)

    for coin in COINS:
        price = load_price(coin, "5m")
        if price is None or len(price) < 500:
            print(f"\n{coin}: 数据不足，跳过")
            continue

        print(f"\n{'─' * 70}")
        print(f"  {coin}  |  总数据量: {len(price)} 根 5m K线")
        print(f"  时间范围: {price.index[0]} ~ {price.index[-1]}")
        print(f"{'─' * 70}")
        print(f"  {'段':>6} | {'入场':>4} | {'出场':>4} | {'总收益%':>8} | {'夏普':>6} | {'最大回撤%':>9} | {'胜率%':>6} | {'盈亏比':>6}")
        print(f"  {'─' * 65}")

        segments = split_3_segments(price)
        for seg_label, seg_price in segments:
            if len(seg_price) < 300:
                print(f"  {seg_label:>6} | 数据不足 ({len(seg_price)} 根)")
                continue

            entries, exits = vol_squeeze_5m_signal(seg_price)
            n_entries = int(entries.sum())
            n_exits = int(exits.sum())

            if n_entries == 0:
                print(f"  {seg_label:>6} | {n_entries:>4} | {n_exits:>4} |   无交易")
                continue

            pf = engine.run(seg_price, entries, exits)
            m = compute_metrics(pf)

            total_ret = m.get("total_return_pct", 0.0)
            sharpe = m.get("sharpe", 0.0)
            max_dd = m.get("max_drawdown_pct", 0.0)
            win_rate = m.get("win_rate_pct", 0.0)
            profit_factor = m.get("profit_factor", 0.0)

            print(
                f"  {seg_label:>6} | {n_entries:>4} | {n_exits:>4} | "
                f"{total_ret:>+8.2f} | {sharpe:>6.2f} | {max_dd:>9.2f} | "
                f"{win_rate:>6.1f} | {profit_factor:>6.2f}"
            )

    print(f"\n{'=' * 80}")
    print("  验证完成")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
