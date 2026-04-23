"""分析信号质量：什么条件下信号更准？"""
import sys
from pathlib import Path
import pandas as pd, numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings
from src.strategies.minute_swing import MinuteSwingStrategy

settings = get_settings()
writer = ParquetWriter(settings.parquet_dir)
strat = MinuteSwingStrategy()
params = dict(trend_ma=180, stop_pct=2.0, take_profit_pct=8.0, min_gap=144)

all_features = []
for sym in ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"]:
    df = writer.read_ohlcv(sym, "5m")
    if df.is_empty(): continue
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    price = pdf.set_index("datetime")["close"]

    e, x = strat.generate_signals(price, **params)
    ei_list = e[e].index
    xi_list = x[x].index

    delta = price.diff()
    g = delta.clip(lower=0).rolling(14).mean()
    l = (-delta).clip(lower=0).rolling(14).mean()
    rsi = 100 - 100/(1+g/l)
    ma = price.rolling(180).mean()

    for ei in ei_list:
        nx = xi_list[xi_list > ei]
        if len(nx) == 0: continue
        xi = nx[0]
        ret = (price.loc[xi] - price.loc[ei]) / price.loc[ei] * 100
        loc = price.index.get_loc(ei)
        all_features.append({
            "sym": sym, "ret": ret, "win": ret > 0,
            "rsi": rsi.iloc[loc],
            "trend_str": (price.iloc[loc] - ma.iloc[loc]) / ma.iloc[loc] * 100,
        })

fdf = pd.DataFrame(all_features)
print(f"Total: {len(fdf)} trades, Win: {fdf['win'].mean()*100:.0f}%\n")

print("=== RSI at entry vs win rate ===")
for lo, hi in [(0,35),(35,45),(45,55),(55,65),(65,80)]:
    sub = fdf[(fdf["rsi"]>=lo)&(fdf["rsi"]<hi)]
    if len(sub) >= 5:
        print(f"  RSI {lo:2d}-{hi:2d}: {len(sub):3d} trades | win:{sub['win'].mean()*100:4.0f}% | avg ret:{sub['ret'].mean():+.2f}%")

print("\n=== Trend strength vs win rate ===")
for lo, hi in [(0,1),(1,2),(2,4),(4,8),(8,30)]:
    sub = fdf[(fdf["trend_str"]>=lo)&(fdf["trend_str"]<hi)]
    if len(sub) >= 5:
        print(f"  str {lo:2d}-{hi:2d}%: {len(sub):3d} trades | win:{sub['win'].mean()*100:4.0f}% | avg ret:{sub['ret'].mean():+.2f}%")
