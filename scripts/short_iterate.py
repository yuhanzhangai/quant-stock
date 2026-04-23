"""做空策略迭代优化器。

每轮迭代：
1. 在 4 币种 5m 数据上测试策略参数变体
2. 按 avg sharpe 排名
3. 输出最优参数组合
4. 记录每轮结果到文件

聚焦前 4 个策略：
- trend_follow（冠军）
- short_swing（SOL验证过）
- rsi_overbought（一致性好）
- 新策略：short_volume_climax（量价极端做空）

运行：uv run python scripts/short_iterate.py
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime

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
from src.strategies.short_swing_trail import ShortSwingTrailStrategy
from src.strategies.short_rsi_overbought import ShortRSIOverboughtStrategy
from src.strategies.short_trend_follow import ShortTrendFollowStrategy


COINS = [
    "ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT",
    "DOT-USDT", "OP-USDT", "SUI-USDT", "ATOM-USDT",
    "PEPE-USDT", "FIL-USDT",
]
RESULTS_FILE = Path("data/short_iteration_results.json")

# 确保目录存在
RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)


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
    for i, label in enumerate(["S1", "S2", "S3"]):
        start = i * seg_len
        end = (i + 1) * seg_len if i < 2 else n
        segments.append((label, price.iloc[start:end]))
    return segments


def test_strategy(strat, params: dict, coins_data: dict, engine: BacktestEngine) -> dict:
    """测试一个策略变体在所有币种所有段上的表现。"""
    sharpes = []
    returns = []
    positive_segs = 0
    total_segs = 0
    total_trades = 0
    per_coin: dict[str, dict] = {}

    for coin_short, price in coins_data.items():
        coin_sharpes = []
        coin_returns = []
        coin_trades = 0
        coin_pos = 0

        segments = split_3_segments(price)
        for seg_label, seg_price in segments:
            if len(seg_price) < 300:
                continue
            total_segs += 1

            try:
                entries, exits = strat.generate_signals(seg_price, **params)
            except Exception:
                sharpes.append(0.0)
                returns.append(0.0)
                coin_sharpes.append(0.0)
                coin_returns.append(0.0)
                continue

            n_entries = int(entries.sum())
            if n_entries == 0:
                sharpes.append(0.0)
                returns.append(0.0)
                coin_sharpes.append(0.0)
                coin_returns.append(0.0)
                continue

            total_trades += n_entries
            coin_trades += n_entries
            price_inv = invert_price(seg_price)
            pf = engine.run(price_inv, entries, exits)
            m = compute_metrics(pf)

            sharpes.append(m["sharpe_ratio"])
            returns.append(m["total_return_pct"])
            coin_sharpes.append(m["sharpe_ratio"])
            coin_returns.append(m["total_return_pct"])
            if m["total_return_pct"] > 0:
                positive_segs += 1
                coin_pos += 1

        per_coin[coin_short] = {
            "avg_sharpe": float(np.mean(coin_sharpes)) if coin_sharpes else 0.0,
            "avg_return": float(np.mean(coin_returns)) if coin_returns else 0.0,
            "positive_segs": coin_pos,
            "total_segs": len(coin_sharpes),
            "trades": coin_trades,
        }

    avg_sharpe = np.mean(sharpes) if sharpes else 0.0
    avg_return = np.mean(returns) if returns else 0.0

    return {
        "avg_sharpe": float(avg_sharpe),
        "avg_return": float(avg_return),
        "positive_segs": positive_segs,
        "total_segs": total_segs,
        "total_trades": total_trades,
        "per_coin": per_coin,
    }


def load_previous_results() -> list:
    """加载之前的迭代结果。"""
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            return json.load(f)
    return []


def save_results(results: list) -> None:
    """保存结果。"""
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)


def run_iteration(iteration: int) -> dict:
    """运行一轮迭代。"""
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")

    # 加载数据
    coins_data = {}
    for coin in COINS:
        price = load_price(coin, "5m")
        if price is not None and len(price) >= 500:
            coins_data[coin.replace("-USDT", "")] = price

    if not coins_data:
        return {"error": "no data"}

    # 定义参数搜索空间（每轮随机选择一些参数）
    np.random.seed(int(time.time()) % 2**31)

    candidates = []

    # === 策略1: trend_follow 变体 ===
    # 第一轮冠军: fast=84 slow=180 gap=288 trail=1.0 (Sharpe=+1.914)
    tf_strat = ShortTrendFollowStrategy()
    tf_params_list = [
        # 冠军参数
        {"fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 冠军附近微调
        {"fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 2.5, "take_profit_pct": 8.0, "trail_pct": 1.0},
        {"fast_ma": 84, "slow_ma": 180, "min_gap": 240, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        {"fast_ma": 84, "slow_ma": 200, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 12.0, "trail_pct": 1.0},
        {"fast_ma": 96, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 0.8},
        {"fast_ma": 72, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 原始 baseline
        {"fast_ma": 60, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 2.0},
        # 随机变体（在冠军附近扰动）
        {
            "fast_ma": int(np.random.choice([72, 84, 96, 108])),
            "slow_ma": int(np.random.choice([160, 180, 200, 220])),
            "min_gap": int(np.random.choice([240, 288, 336])),
            "stop_pct": float(np.random.choice([2.5, 3.0, 3.5])),
            "take_profit_pct": float(np.random.choice([8.0, 10.0, 12.0])),
            "trail_pct": float(np.random.choice([0.8, 1.0, 1.2, 1.5])),
        },
        {
            "fast_ma": int(np.random.choice([72, 84, 96, 108])),
            "slow_ma": int(np.random.choice([160, 180, 200, 220])),
            "min_gap": int(np.random.choice([240, 288, 336])),
            "stop_pct": float(np.random.choice([2.5, 3.0, 3.5])),
            "take_profit_pct": float(np.random.choice([8.0, 10.0, 12.0])),
            "trail_pct": float(np.random.choice([0.8, 1.0, 1.2, 1.5])),
        },
    ]
    for params in tf_params_list:
        candidates.append(("trend_follow", tf_strat, params))

    # === 策略2: short_swing 变体 ===
    # 第一轮亚军: ma=180 rsi=55 gap=288 tp=6.0 (Sharpe=+0.777)
    ss_strat = ShortSwingStrategy()
    ss_params_list = [
        # 亚军参数
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.0, "take_profit_pct": 6.0},
        # 亚军附近微调
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 1.5, "take_profit_pct": 6.0},
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.0, "take_profit_pct": 5.0},
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.0, "take_profit_pct": 7.0},
        {"trend_ma": 180, "rsi_entry": 50, "min_gap": 288, "stop_pct": 2.0, "take_profit_pct": 6.0},
        {"trend_ma": 200, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.0, "take_profit_pct": 6.0},
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 240, "stop_pct": 2.0, "take_profit_pct": 6.0},
        # 随机
        {
            "trend_ma": int(np.random.choice([144, 180, 200, 220])),
            "rsi_entry": int(np.random.choice([50, 55, 58, 60])),
            "min_gap": int(np.random.choice([240, 288, 336])),
            "stop_pct": float(np.random.choice([1.5, 2.0, 2.5])),
            "take_profit_pct": float(np.random.choice([5.0, 6.0, 7.0, 8.0])),
        },
    ]
    for params in ss_params_list:
        candidates.append(("short_swing", ss_strat, params))

    # === 策略3: rsi_overbought 变体 ===
    rsi_strat = ShortRSIOverboughtStrategy()
    rsi_params_list = [
        {"trend_ma": 180, "rsi_overbought": 70, "rsi_entry_cross": 65, "min_gap": 144, "stop_pct": 2.0, "take_profit_pct": 5.0},
        {"trend_ma": 180, "rsi_overbought": 65, "rsi_entry_cross": 60, "min_gap": 144, "stop_pct": 2.0, "take_profit_pct": 5.0},
        {"trend_ma": 120, "rsi_overbought": 70, "rsi_entry_cross": 65, "min_gap": 192, "stop_pct": 2.5, "take_profit_pct": 6.0},
        {"trend_ma": 180, "rsi_overbought": 65, "rsi_entry_cross": 55, "min_gap": 192, "stop_pct": 2.0, "take_profit_pct": 8.0},
        {"trend_ma": 144, "rsi_overbought": 60, "rsi_entry_cross": 55, "min_gap": 144, "stop_pct": 1.5, "take_profit_pct": 4.0},
        # 随机
        {
            "trend_ma": int(np.random.choice([120, 144, 180, 200])),
            "rsi_overbought": int(np.random.choice([60, 65, 70, 75])),
            "rsi_entry_cross": int(np.random.choice([50, 55, 60, 65])),
            "min_gap": int(np.random.choice([96, 144, 192, 240])),
            "stop_pct": float(np.random.choice([1.5, 2.0, 2.5])),
            "take_profit_pct": float(np.random.choice([4.0, 5.0, 6.0, 8.0])),
        },
    ]
    for params in rsi_params_list:
        candidates.append(("rsi_overbought", rsi_strat, params))

    # === 策略4: short_swing_trail（trailing stop 版做空波段）===
    st_strat = ShortSwingTrailStrategy()
    st_params_list = [
        # 冠军参数
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.5, "trail_pct": 1.5, "min_profit": 2.0},
        # 冠军附近微调
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.0, "trail_pct": 1.5, "min_profit": 1.0},
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.0, "trail_pct": 2.0, "min_profit": 1.5},
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 3.0, "trail_pct": 1.0, "min_profit": 1.0},
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.5, "trail_pct": 1.5, "min_profit": 1.5},
        {"trend_ma": 200, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.5, "trail_pct": 1.5, "min_profit": 2.0},
        # 随机
        {
            "trend_ma": int(np.random.choice([144, 180, 200, 220])),
            "rsi_entry": int(np.random.choice([50, 55, 58, 60])),
            "min_gap": int(np.random.choice([240, 288, 336])),
            "stop_pct": float(np.random.choice([2.0, 2.5, 3.0])),
            "trail_pct": float(np.random.choice([1.0, 1.5, 2.0])),
            "min_profit": float(np.random.choice([0.5, 1.0, 1.5, 2.0])),
        },
    ]
    for params in st_params_list:
        candidates.append(("swing_trail", st_strat, params))

    # 测试所有候选
    results = []
    for name, strat, params in candidates:
        r = test_strategy(strat, params, coins_data, engine)
        r["strategy"] = name
        r["params"] = params
        results.append(r)

    # 按 avg_sharpe 排序
    results.sort(key=lambda x: x["avg_sharpe"], reverse=True)

    # 输出本轮结果
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'=' * 90}")
    print(f"  迭代 #{iteration} | {now} | 测试了 {len(candidates)} 个参数组合")
    print(f"{'=' * 90}")
    print(
        f"  {'排名':>4} | {'策略':<18} | {'Avg夏普':>8} | {'Avg收益%':>9} | "
        f"{'正收益段':>8} | {'交易数':>6} | 关键参数"
    )
    print(f"  {'─' * 85}")

    for rank, r in enumerate(results[:15], 1):
        pos_str = f"{r['positive_segs']}/{r['total_segs']}"
        # 精简参数显示
        p = r["params"]
        if r["strategy"] == "trend_follow":
            key = f"fast={p['fast_ma']} slow={p['slow_ma']} gap={p['min_gap']} trail={p['trail_pct']}"
        elif r["strategy"] == "short_swing":
            key = f"ma={p['trend_ma']} rsi={p['rsi_entry']} gap={p['min_gap']} tp={p['take_profit_pct']}"
        elif r["strategy"] == "rsi_overbought":
            key = f"ob={p['rsi_overbought']} cross={p['rsi_entry_cross']} gap={p['min_gap']} tp={p['take_profit_pct']}"
        elif r["strategy"] == "swing_trail":
            key = f"trail={p.get('trail_pct','-')} minp={p.get('min_profit','-')} gap={p['min_gap']} stop={p.get('stop_pct','-')}"
        else:
            key = f"gap={p['min_gap']} stop={p.get('stop_pct','-')} tp={p.get('take_profit_pct','-')}"

        marker = " ***" if rank <= 3 else ""
        print(
            f"  {rank:>4} | {r['strategy']:<18} | {r['avg_sharpe']:>+8.3f} | "
            f"{r['avg_return']:>+9.2f} | {pos_str:>8} | {r['total_trades']:>6} | {key}{marker}"
        )

    # 最佳结果
    best = results[0]
    print(f"\n  冠军: {best['strategy']} | Sharpe={best['avg_sharpe']:+.3f} | {best['params']}")

    # === 币种优势策略 ===
    print(f"\n  {'─' * 85}")
    print(f"  币种优势策略（该策略在某币上表现特别突出）")
    print(f"  {'─' * 85}")

    coin_champions: dict[str, dict] = {}
    for r in results:
        for coin_short, coin_data in r.get("per_coin", {}).items():
            if coin_data["avg_sharpe"] > 1.0 and coin_data["positive_segs"] >= 2:
                if coin_short not in coin_champions or coin_data["avg_sharpe"] > coin_champions[coin_short]["sharpe"]:
                    coin_champions[coin_short] = {
                        "strategy": r["strategy"],
                        "sharpe": coin_data["avg_sharpe"],
                        "avg_return": coin_data["avg_return"],
                        "positive_segs": coin_data["positive_segs"],
                        "total_segs": coin_data["total_segs"],
                        "params": r["params"],
                    }

    for coin_short in sorted(coin_champions.keys()):
        c = coin_champions[coin_short]
        pos_str = f"{c['positive_segs']}/{c['total_segs']}"
        print(
            f"  {coin_short:<6} -> {c['strategy']:<18} | Sharpe={c['sharpe']:>+6.2f} | "
            f"Ret={c['avg_return']:>+6.2f}% | 正收益={pos_str} | ★优势策略"
        )

    # 没有优势策略的币种
    all_coins = set()
    for r in results:
        all_coins.update(r.get("per_coin", {}).keys())
    for coin_short in sorted(all_coins - set(coin_champions.keys())):
        # 找该币上最好的策略
        best_for_coin = None
        for r in results:
            cd = r.get("per_coin", {}).get(coin_short)
            if cd and (best_for_coin is None or cd["avg_sharpe"] > best_for_coin["sharpe"]):
                best_for_coin = {
                    "strategy": r["strategy"],
                    "sharpe": cd["avg_sharpe"],
                    "avg_return": cd["avg_return"],
                    "positive_segs": cd["positive_segs"],
                    "total_segs": cd["total_segs"],
                }
        if best_for_coin:
            pos_str = f"{best_for_coin['positive_segs']}/{best_for_coin['total_segs']}"
            print(
                f"  {coin_short:<6} -> {best_for_coin['strategy']:<18} | Sharpe={best_for_coin['sharpe']:>+6.2f} | "
                f"Ret={best_for_coin['avg_return']:>+6.2f}% | 正收益={pos_str} | (最优但未达标)"
            )

    return {
        "iteration": iteration,
        "timestamp": now,
        "best_strategy": best["strategy"],
        "best_sharpe": best["avg_sharpe"],
        "best_return": best["avg_return"],
        "best_params": best["params"],
        "top3": [
            {"strategy": r["strategy"], "sharpe": r["avg_sharpe"], "params": r["params"]}
            for r in results[:3]
        ],
        "coin_champions": coin_champions,
    }


def main() -> None:
    """单次运行。"""
    previous = load_previous_results()
    iteration = len(previous) + 1

    result = run_iteration(iteration)

    previous.append(result)
    save_results(previous)

    # 显示历史最优
    if len(previous) > 1:
        best_ever = max(previous, key=lambda x: x.get("best_sharpe", 0))
        print(f"\n  历史最优: 迭代#{best_ever['iteration']} | {best_ever['best_strategy']} | "
              f"Sharpe={best_ever['best_sharpe']:+.3f}")

    print(f"\n  结果已保存到 {RESULTS_FILE}")


if __name__ == "__main__":
    main()
