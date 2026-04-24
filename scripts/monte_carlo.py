"""Monte Carlo 模拟：MinSwing v3 的真实收益预期。"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.storage.parquet_writer import ParquetWriter
from src.strategies.minswing_v3_final import minswing_v3_signal

settings = get_settings()
writer = ParquetWriter(settings.parquet_dir)

# 收集所有真实交易的收益率
all_returns = []

for sym, coin in [("ETH-USDT", "ETH"), ("SOL-USDT", "SOL"), ("NEAR-USDT", "NEAR"), ("ARB-USDT", "ARB")]:
    df = writer.read_ohlcv(sym, "5m")
    if df.is_empty():
        continue
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    price = pdf.set_index("datetime")["close"]

    e, x = minswing_v3_signal(price, coin=coin)
    entries_idx = e[e].index
    exits_idx = x[x].index

    for ei in entries_idx:
        next_exits = exits_idx[exits_idx > ei]
        if len(next_exits) > 0:
            xi = next_exits[0]
            ret = (price.loc[xi] - price.loc[ei]) / price.loc[ei] * 100
            all_returns.append(ret)

rets = np.array(all_returns)
print(f"历史交易: {len(rets)} 笔")
print(f"平均收益: {np.mean(rets):+.2f}%")
print(f"中位收益: {np.median(rets):+.2f}%")
print(f"胜率: {(rets > 0).mean() * 100:.0f}%")
print(f"平均盈利: {np.mean(rets[rets > 0]):+.2f}%")
print(f"平均亏损: {np.mean(rets[rets < 0]):+.2f}%")

# Monte Carlo
print("\n=== Monte Carlo 1000x (每次 20 笔, $50x5x) ===")
np.random.seed(42)
finals = []
for _ in range(1000):
    sample = np.random.choice(rets, size=20, replace=True)
    equity = 250.0
    for r in sample:
        equity *= 1 + r / 100
    finals.append(equity)

fv = np.array(finals)
profit = (fv - 250) / 50 * 100

for p in [5, 25, 50, 75, 95]:
    val = np.percentile(profit, p)
    print(f"  {p:2d}% 分位: {val:+.0f}%")
print(f"  盈利概率: {(profit > 0).mean() * 100:.0f}%")
print(f"  翻倍概率: {(profit > 100).mean() * 100:.0f}%")
