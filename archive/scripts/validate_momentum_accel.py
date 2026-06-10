"""动量加速度策略 -- 4 币种 x 3 段验证 (5m)"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loguru import logger
from src.strategies.momentum_acceleration import momentum_acceleration_signal

from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics

logger.remove()

# ── Config ──
COINS = ["ETH", "SOL", "NEAR", "ARB"]
PARAMS = {
    "ETH": dict(velocity_window=36, accel_window=18, trend_ma=240, min_gap=192, stop_pct=2.0, take_profit_pct=8.0),
    "SOL": dict(velocity_window=36, accel_window=18, trend_ma=200, min_gap=144, stop_pct=2.0, take_profit_pct=8.0),
    "NEAR": dict(velocity_window=36, accel_window=18, trend_ma=200, min_gap=144, stop_pct=2.0, take_profit_pct=8.0),
    "ARB": dict(velocity_window=36, accel_window=18, trend_ma=200, min_gap=144, stop_pct=2.0, take_profit_pct=8.0),
}
INIT_CASH = 250.0
LEVERAGE = 5


def load_price(coin):
    df = pd.read_parquet(f"data/parquet/ohlcv/spot/{coin}-USDT/5m/2026.parquet")
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("datetime").sort_index()
    return df["close"]


def split_3seg(price):
    n = len(price)
    s1 = n // 3
    s2 = 2 * n // 3
    return [price.iloc[:s1], price.iloc[s1:s2], price.iloc[s2:]]


def run_backtest(price_seg, params):
    entries, exits = momentum_acceleration_signal(price_seg, **params)
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=INIT_CASH, freq="5min")
    pf = engine.run(price_seg, entries, exits)
    return compute_metrics(pf)


# ── Run ──
results = []

for coin in COINS:
    price = load_price(coin)
    segs = split_3seg(price)
    seg_labels = [f"{s.index[0].strftime('%m/%d')}-{s.index[-1].strftime('%m/%d')}" for s in segs]

    for seg_i, (seg, label) in enumerate(zip(segs, seg_labels, strict=False)):
        m = run_backtest(seg, PARAMS[coin])
        results.append(
            {
                "coin": coin,
                "segment": f"S{seg_i + 1}({label})",
                "trades": m["total_trades"],
                "win_rate": m["win_rate_pct"],
                "return_pct": m["total_return_pct"],
                "sharpe": m["sharpe_ratio"],
                "max_dd": m["max_drawdown_pct"],
                "final_val": m["final_value"],
            }
        )

df = pd.DataFrame(results)
df["lev_return_pct"] = df["return_pct"] * LEVERAGE
df["lev_profit_usd"] = INIT_CASH * df["return_pct"] / 100 * LEVERAGE

# ── Print ──
W = 130
print("=" * W)
print(f"{'Momentum Acceleration 3段验证 (5m)':^{W}}")
print(f"{'动量加速度策略 | OKX_SWAP | init=$250 | 5x leverage':^{W}}")
print("=" * W)

for coin in COINS:
    p = PARAMS[coin]
    print(f"\n{'─' * W}")
    print(
        f"  {coin}-USDT  |  vel_w={p['velocity_window']}, acc_w={p['accel_window']}, "
        f"trend_ma={p['trend_ma']}, gap={p['min_gap']}, "
        f"stop={p['stop_pct']}%, tp={p['take_profit_pct']}%"
    )
    print(f"{'─' * W}")
    hdr = f"  {'Segment':^24} {'#Tr':>4} {'WR%':>6} {'Ret%':>8} {'Sharpe':>7} {'MaxDD%':>7} {'5xRet%':>9} {'5xP&L$':>9}"
    print(hdr)
    print(f"  {'─' * 24} {'─' * 4} {'─' * 6} {'─' * 8} {'─' * 7} {'─' * 7} {'─' * 9} {'─' * 9}")

    coin_df = df[df["coin"] == coin]
    for _, row in coin_df.iterrows():
        print(
            f"  {row['segment']:^24} {row['trades']:>4} "
            f"{row['win_rate']:>6.1f} {row['return_pct']:>+8.2f} {row['sharpe']:>7.2f} "
            f"{row['max_dd']:>7.2f} {row['lev_return_pct']:>+9.2f} "
            f"{row['lev_profit_usd']:>+9.2f}"
        )

    tot = coin_df.agg(
        {
            "trades": "sum",
            "win_rate": "mean",
            "return_pct": "sum",
            "sharpe": "mean",
            "lev_return_pct": "sum",
            "lev_profit_usd": "sum",
        }
    )
    print(
        f"  {'TOTAL':^24} {int(tot['trades']):>4} "
        f"{tot['win_rate']:>6.1f} {tot['return_pct']:>+8.2f} {tot['sharpe']:>7.2f} "
        f"{'':>7} {tot['lev_return_pct']:>+9.2f} "
        f"{tot['lev_profit_usd']:>+9.2f}"
    )

# ── Grand Summary ──
print(f"\n{'=' * W}")
print(f"{'Grand Summary':^{W}}")
print(f"{'=' * W}")
print(f"  {'Coin':^8} {'Trades':>6} {'AvgWR%':>7} {'TotRet%':>9} {'AvgSharpe':>9} {'5xTotRet%':>10} {'5xP&L$':>10}")
print(f"  {'─' * 8} {'─' * 6} {'─' * 7} {'─' * 9} {'─' * 9} {'─' * 10} {'─' * 10}")

grand_pnl = 0
for coin in COINS:
    c = df[df["coin"] == coin]
    tot_tr = c["trades"].sum()
    avg_wr = c["win_rate"].mean()
    tot_ret = c["return_pct"].sum()
    avg_sh = c["sharpe"].mean()
    tot_lev = c["lev_return_pct"].sum()
    tot_pnl = c["lev_profit_usd"].sum()
    grand_pnl += tot_pnl
    print(f"  {coin:^8} {tot_tr:>6} {avg_wr:>7.1f} {tot_ret:>+9.2f} {avg_sh:>9.2f} {tot_lev:>+10.2f} {tot_pnl:>+10.2f}")

print(f"\n  Grand Total 5x P&L: ${grand_pnl:+.2f}")
print(f"{'=' * W}")
