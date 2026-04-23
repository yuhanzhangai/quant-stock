"""做空波段策略 (short_swing) 三段验证。

在 ETH/SOL/NEAR/ARB 的 5m 数据上做 3 段回测验证。
使用"反转价格"技巧让 vectorbt 做多反转价格 = 模拟做空原始价格。
OKX_SWAP 费率，init_cash=250。
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


COINS = ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"]


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
    strategy = ShortSwingStrategy()
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")

    print("=" * 85)
    print("  做空波段策略 (short_swing) — 三段验证")
    print("  费率: OKX_SWAP | 初始资金: $250 | 反转价格模拟做空")
    print("=" * 85)

    for coin in COINS:
        price = load_price(coin, "5m")
        if price is None or len(price) < 500:
            print(f"\n{coin}: 数据不足，跳过")
            continue

        print(f"\n{'─' * 80}")
        print(f"  {coin}  |  总数据量: {len(price)} 根 5m K线")
        print(f"  时间范围: {price.index[0]} ~ {price.index[-1]}")
        print(f"{'─' * 80}")
        print(
            f"  {'段':>6} | {'入场':>4} | {'出场':>4} | "
            f"{'总收益%':>8} | {'夏普':>6} | {'最大回撤%':>9} | "
            f"{'胜率%':>6} | {'终值$':>8}"
        )
        print(f"  {'─' * 75}")

        segments = split_3_segments(price)
        for seg_label, seg_price in segments:
            if len(seg_price) < 300:
                print(f"  {seg_label:>6} | 数据不足 ({len(seg_price)} 根)")
                continue

            # 1) 用原始价格生成做空信号
            entries, exits = strategy.generate_signals(seg_price)
            n_entries = int(entries.sum())
            n_exits = int(exits.sum())

            if n_entries == 0:
                print(f"  {seg_label:>6} | {n_entries:>4} | {n_exits:>4} |   无交易")
                continue

            # 2) 反转价格，用做多回测引擎模拟做空
            price_inv = invert_price(seg_price)

            pf = engine.run(price_inv, entries, exits)
            m = compute_metrics(pf)

            total_ret = m.get("total_return_pct", 0.0)
            sharpe = m.get("sharpe_ratio", 0.0)
            max_dd = m.get("max_drawdown_pct", 0.0)
            win_rate = m.get("win_rate_pct", 0.0)
            final_val = m.get("final_value", 0.0)

            print(
                f"  {seg_label:>6} | {n_entries:>4} | {n_exits:>4} | "
                f"{total_ret:>+8.2f} | {sharpe:>6.2f} | {max_dd:>9.2f} | "
                f"{win_rate:>6.1f} | {final_val:>8.2f}"
            )

    print(f"\n{'=' * 85}")
    print("  验证完成")
    print(f"{'=' * 85}")


if __name__ == "__main__":
    main()
