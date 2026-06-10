"""3段验证: 旧参数 vs 新参数(精细搜索最优) -- 4币种对比"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loguru import logger

from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.strategies.minute_swing import minute_swing_signal

logger.remove()

# ── Config ──
COINS = ["ETH", "SOL", "NEAR", "ARB"]
NEW_PARAMS = {
    "ETH": dict(trend_ma=210, stop_pct=2.0, take_profit_pct=5.0, min_gap=192),
    "SOL": dict(trend_ma=180, stop_pct=2.0, take_profit_pct=8.0, min_gap=192),
    "NEAR": dict(trend_ma=180, stop_pct=2.0, take_profit_pct=10.0, min_gap=192),
    "ARB": dict(trend_ma=180, stop_pct=2.0, take_profit_pct=8.0, min_gap=192),
}
OLD_PARAMS = {
    "ETH": dict(trend_ma=210, stop_pct=2.0, take_profit_pct=8.0, min_gap=144),
    "SOL": dict(trend_ma=180, stop_pct=2.0, take_profit_pct=8.0, min_gap=144),
    "NEAR": dict(trend_ma=180, stop_pct=2.0, take_profit_pct=8.0, min_gap=144),
    "ARB": dict(trend_ma=180, stop_pct=2.0, take_profit_pct=8.0, min_gap=144),
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
    entries, exits = minute_swing_signal(price_seg, **params)
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=INIT_CASH, freq="5min")
    pf = engine.run(price_seg, entries, exits)
    return compute_metrics(pf)


# ── Run all ──
results = []

for coin in COINS:
    price = load_price(coin)
    segs = split_3seg(price)
    seg_labels = [f"{s.index[0].strftime('%m/%d')}-{s.index[-1].strftime('%m/%d')}" for s in segs]

    for seg_i, (seg, label) in enumerate(zip(segs, seg_labels, strict=False)):
        m_old = run_backtest(seg, OLD_PARAMS[coin])
        m_new = run_backtest(seg, NEW_PARAMS[coin])

        for typ, params, m in [("OLD", OLD_PARAMS[coin], m_old), ("NEW", NEW_PARAMS[coin], m_new)]:
            results.append(
                {
                    "coin": coin,
                    "segment": f"S{seg_i + 1}({label})",
                    "type": typ,
                    "tp": params["take_profit_pct"],
                    "gap": params["min_gap"],
                    "trades": m["total_trades"],
                    "win_rate": m["win_rate_pct"],
                    "return_pct": m["total_return_pct"],
                    "sharpe": m["sharpe_ratio"],
                    "max_dd": m["max_drawdown_pct"],
                    "final_val": m["final_value"],
                }
            )

df_results = pd.DataFrame(results)
df_results["lev_return_pct"] = df_results["return_pct"] * LEVERAGE
df_results["lev_profit_usd"] = INIT_CASH * df_results["return_pct"] / 100 * LEVERAGE

# ── Print ──
W = 135
print("=" * W)
print(f"{'minute_swing 3段验证 -- 旧参数 vs 新参数 (精细搜索最优)':^{W}}")
print(f"{'OKX_SWAP | init_cash=$250 | $50 x 5x leverage':^{W}}")
print("=" * W)

for coin in COINS:
    op = OLD_PARAMS[coin]
    np_ = NEW_PARAMS[coin]
    print(f"\n{'─' * W}")
    print(f"  {coin}-USDT")
    print(f"  OLD: trend_ma={op['trend_ma']}, stop={op['stop_pct']}%, tp={op['take_profit_pct']}%, gap={op['min_gap']}")
    print(
        f"  NEW: trend_ma={np_['trend_ma']}, stop={np_['stop_pct']}%, tp={np_['take_profit_pct']}%, gap={np_['min_gap']}"
    )
    print(f"{'─' * W}")
    hdr = f"  {'Segment':^24} {'Type':^5} {'#Tr':>4} {'WR%':>6} {'Ret%':>8} {'Sharpe':>7} {'MaxDD%':>7} {'5xRet%':>9} {'5xP&L$':>9}"
    print(hdr)
    print(f"  {'─' * 24} {'─' * 5} {'─' * 4} {'─' * 6} {'─' * 8} {'─' * 7} {'─' * 7} {'─' * 9} {'─' * 9}")

    coin_df = df_results[df_results["coin"] == coin]
    for seg_name in coin_df["segment"].unique():
        seg_df = coin_df[coin_df["segment"] == seg_name]
        for _, row in seg_df.iterrows():
            star = "*" if row["type"] == "NEW" else " "
            print(
                f" {star} {row['segment']:^24} {row['type']:^5} {row['trades']:>4} "
                f"{row['win_rate']:>6.1f} {row['return_pct']:>+8.2f} {row['sharpe']:>7.2f} "
                f"{row['max_dd']:>7.2f} {row['lev_return_pct']:>+9.2f} "
                f"{row['lev_profit_usd']:>+9.2f}"
            )
        print()

# ── Summary ──
print(f"{'=' * W}")
print(f"{'Summary by Coin (3-seg total)':^{W}}")
print(f"{'=' * W}")
print(
    f"  {'Coin':^8} {'Type':^5} {'Trades':>6} {'AvgWR%':>7} {'TotRet%':>9} {'AvgSharpe':>9} {'5xTotRet%':>10} {'5xTotP&L$':>10} {'Result':>8}"
)
print(f"  {'─' * 8} {'─' * 5} {'─' * 6} {'─' * 7} {'─' * 9} {'─' * 9} {'─' * 10} {'─' * 10} {'─' * 8}")

total_old = 0
total_new = 0

for coin in COINS:
    coin_df = df_results[df_results["coin"] == coin]
    for typ in ["OLD", "NEW"]:
        t = coin_df[coin_df["type"] == typ]
        tot_tr = t["trades"].sum()
        avg_wr = t["win_rate"].mean()
        tot_ret = t["return_pct"].sum()
        avg_sh = t["sharpe"].mean()
        tot_lev = t["lev_return_pct"].sum()
        tot_pnl = t["lev_profit_usd"].sum()

        if typ == "OLD":
            total_old += tot_pnl
        else:
            total_new += tot_pnl

        old_r = coin_df[coin_df["type"] == "OLD"]["return_pct"].sum()
        new_r = coin_df[coin_df["type"] == "NEW"]["return_pct"].sum()
        verdict = ""
        if typ == "NEW":
            verdict = ">> WIN" if new_r > old_r else "LOSE"

        print(
            f"  {coin:^8} {typ:^5} {tot_tr:>6} {avg_wr:>7.1f} {tot_ret:>+9.2f} "
            f"{avg_sh:>9.2f} {tot_lev:>+10.2f} {tot_pnl:>+10.2f} {verdict:>8}"
        )

print(f"\n{'─' * W}")
print(f"  TOTAL OLD  5x P&L: ${total_old:+.2f}")
print(f"  TOTAL NEW  5x P&L: ${total_new:+.2f}")
diff = total_new - total_old
tag = "[NEW WINS]" if diff > 0 else "[OLD WINS]"
print(f"  Difference:         ${diff:+.2f}  {tag}")
print(f"{'=' * W}")
