"""策略淘汰赛：4→8→16→32，末位淘汰，迭代进化。

⚠️ PAUSED (2026-04-23)
当前阶段不继续扩张策略数量。
只有在完成 validation pipeline (Checkpoint 7) 后，才允许重新启用。
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from config.settings import get_settings
from src.strategies.minute_swing import minute_swing_signal
from src.strategies.intraday_momentum import intraday_momentum_signal
from src.strategies.minute_swing_dual import minute_swing_dual_signal


def load_price(sym, tf="5m"):
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(sym, tf)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime")["close"]


def evaluate_strategy(name, func, params, coins, engine):
    """在多币种3段上评估策略，返回综合得分。"""
    all_sharpes = []
    all_returns = []
    for sym in coins:
        price = load_price(sym)
        if price is None:
            continue
        n = len(price)
        seg = n // 3
        for i in range(3):
            chunk = price.iloc[i * seg : (i + 1) * seg]
            try:
                e, x = func(chunk, **params)
                pf = engine.run(chunk, e, x)
                m = compute_metrics(pf)
                all_sharpes.append(m["sharpe_ratio"])
                all_returns.append(m["total_return_pct"])
            except Exception:
                all_sharpes.append(0)
                all_returns.append(0)

    avg_sharpe = np.mean(all_sharpes) if all_sharpes else -99
    avg_return = np.mean(all_returns) if all_returns else -99
    pos_rate = sum(1 for s in all_sharpes if s > 0) / max(len(all_sharpes), 1)

    return {
        "name": name,
        "avg_sharpe": round(avg_sharpe, 3),
        "avg_return": round(avg_return, 2),
        "pos_rate": round(pos_rate, 2),
        "n_tests": len(all_sharpes),
        "params": params,
    }


def run_tournament(strategies, coins, generation=1):
    """跑一轮淘汰赛。"""
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")

    logger.info(f"\n{'='*70}")
    logger.info(f"淘汰赛 Generation {generation} | {len(strategies)} 个策略")
    logger.info(f"{'='*70}")

    results = []
    for name, func, params in strategies:
        r = evaluate_strategy(name, func, params, coins, engine)
        results.append(r)

    results.sort(key=lambda x: x["avg_sharpe"], reverse=True)

    for i, r in enumerate(results):
        tag = " ★" if r["avg_sharpe"] > 1 else ""
        logger.info(
            f"  #{i+1:2d} {r['name']:30s} | sharpe:{r['avg_sharpe']:+.3f} "
            f"ret:{r['avg_return']:+.1f}% pos:{r['pos_rate']:.0%}{tag}"
        )

    return results


# ============================================================
# 初始 4 个策略（Generation 1）
# ============================================================
COINS = ["ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT"]

GEN1_STRATEGIES = [
    ("MinSwing_v3_base", minute_swing_signal,
     {"trend_ma": 180, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 144}),
    ("MinSwing_fast", minute_swing_signal,
     {"trend_ma": 120, "stop_pct": 2.0, "take_profit_pct": 6.0, "min_gap": 96}),
    ("IntradayMom_p4", intraday_momentum_signal,
     {"session_bars": 96, "momentum_threshold": 0.008, "hold_bars": 192, "stop_pct": 1.0}),
    ("MinSwing_dual", minute_swing_dual_signal,
     {"trend_ma": 180, "stop_pct": 2.0, "take_profit_pct": 8.0, "min_gap": 144}),
]


def mutate_params(params, mutation_rate=0.2):
    """随机变异参数（±20%）。"""
    new_params = {}
    for k, v in params.items():
        if isinstance(v, (int, float)) and k not in ("stop_pct",):
            delta = v * mutation_rate * (np.random.random() * 2 - 1)
            new_v = v + delta
            if isinstance(v, int):
                new_v = max(1, int(round(new_v)))
            else:
                new_v = round(max(0.1, new_v), 2)
            new_params[k] = new_v
        else:
            new_params[k] = v
    return new_params


def evolve(results, strategies_map, generation):
    """基于结果产生下一代：每个存活策略产生 1 个变异体。"""
    next_gen = []

    for r in results:
        name = r["name"]
        params = r["params"]

        # 找到对应的函数
        func = None
        for n, f, p in strategies_map:
            if n == name:
                func = f
                break
        if func is None:
            # 从名字推断函数
            if "Intraday" in name:
                func = intraday_momentum_signal
            elif "dual" in name.lower():
                func = minute_swing_dual_signal
            else:
                func = minute_swing_signal

        # 保留原版
        next_gen.append((name, func, params))

        # 产生变异体
        mut_params = mutate_params(params)
        mut_name = f"{name}_mut_g{generation}"
        next_gen.append((mut_name, func, mut_params))

    return next_gen


if __name__ == "__main__":
    np.random.seed(int(time.time()) % 10000)

    # Generation 1: 4 个策略
    logger.info("开始淘汰赛！")
    results1 = run_tournament(GEN1_STRATEGIES, COINS, generation=1)

    # 保存结果
    output = Path("reports") / "tournament.json"
    output.parent.mkdir(exist_ok=True)
    with open(output, "w") as f:
        json.dump({"gen1": results1}, f, indent=2, default=str)

    logger.info(f"\nGeneration 1 完成。结果保存到 {output}")
    logger.info("30 分钟后进行 Generation 2（4→8）...")
