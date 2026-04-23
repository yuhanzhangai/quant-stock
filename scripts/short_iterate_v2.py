"""做空策略迭代V2 — 新因子维度探索。

锚点策略（baseline）：
- trend_follow: Sharpe +2.35（冠军）
- swing_trail: Sharpe +1.10

新探索方向：
1. vol_atr: 成交量+ATR波动率（全新因子维度）
2. session: 时段过滤（亚洲时段alpha）
3. multi_tf: 多时间框架确认（future）

运行：uv run python scripts/short_iterate_v2.py
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
from src.strategies.short_swing import invert_price

# 锚点策略
from src.strategies.short_trend_follow import ShortTrendFollowStrategy
from src.strategies.short_swing_trail import ShortSwingTrailStrategy
# 新策略
from src.strategies.short_vol_atr import ShortVolATRStrategy
from src.strategies.short_session_filter import ShortSessionFilterStrategy
from src.strategies.short_multi_tf import ShortMultiTFStrategy


COINS = [
    "ETH-USDT", "SOL-USDT", "NEAR-USDT", "ARB-USDT",
    "DOT-USDT", "OP-USDT", "SUI-USDT", "ATOM-USDT",
    "PEPE-USDT", "FIL-USDT",
]


def load_ohlcv(symbol: str, tf: str) -> pd.DataFrame | None:
    """加载完整 OHLCV 数据（不只是 close）。"""
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, tf)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    pdf = pdf.set_index("datetime").sort_index()
    return pdf


def split_3(price: pd.Series) -> list[tuple[str, pd.Series]]:
    n = len(price)
    seg = n // 3
    return [
        ("S1", price.iloc[:seg]),
        ("S2", price.iloc[seg:2*seg]),
        ("S3", price.iloc[2*seg:]),
    ]


def test_strategy_full(strat, params: dict, coins_ohlcv: dict, engine: BacktestEngine, use_volume: bool = False) -> dict:
    """测试策略，支持传入 volume/high/low。"""
    sharpes, returns = [], []
    positive_segs, total_segs, total_trades = 0, 0, 0
    per_coin: dict[str, dict] = {}

    for coin_short, ohlcv in coins_ohlcv.items():
        price = ohlcv["close"]
        c_sharpes, c_returns, c_trades, c_pos = [], [], 0, 0

        for seg_label, seg_price in split_3(price):
            if len(seg_price) < 300:
                continue
            total_segs += 1

            try:
                extra = {}
                if use_volume:
                    seg_idx = seg_price.index
                    extra["volume"] = ohlcv.loc[seg_idx, "volume"] if "volume" in ohlcv.columns else None
                    extra["high"] = ohlcv.loc[seg_idx, "high"] if "high" in ohlcv.columns else None
                    extra["low"] = ohlcv.loc[seg_idx, "low"] if "low" in ohlcv.columns else None
                entries, exits = strat.generate_signals(seg_price, **params, **extra)
            except Exception as e:
                sharpes.append(0.0); returns.append(0.0)
                c_sharpes.append(0.0); c_returns.append(0.0)
                continue

            n_entries = int(entries.sum())
            if n_entries == 0:
                sharpes.append(0.0); returns.append(0.0)
                c_sharpes.append(0.0); c_returns.append(0.0)
                continue

            total_trades += n_entries; c_trades += n_entries
            price_inv = invert_price(seg_price)
            pf = engine.run(price_inv, entries, exits)
            m = compute_metrics(pf)

            sharpes.append(m["sharpe_ratio"]); returns.append(m["total_return_pct"])
            c_sharpes.append(m["sharpe_ratio"]); c_returns.append(m["total_return_pct"])
            if m["total_return_pct"] > 0:
                positive_segs += 1; c_pos += 1

        per_coin[coin_short] = {
            "avg_sharpe": float(np.mean(c_sharpes)) if c_sharpes else 0.0,
            "avg_return": float(np.mean(c_returns)) if c_returns else 0.0,
            "positive_segs": c_pos,
            "total_segs": len(c_sharpes),
            "trades": c_trades,
        }

    return {
        "avg_sharpe": float(np.mean(sharpes)) if sharpes else 0.0,
        "avg_return": float(np.mean(returns)) if returns else 0.0,
        "positive_segs": positive_segs,
        "total_segs": total_segs,
        "total_trades": total_trades,
        "per_coin": per_coin,
    }


def main() -> None:
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="5min")
    np.random.seed(int(time.time()) % 2**31)

    # 加载 OHLCV（包含 volume）
    coins_ohlcv = {}
    for coin in COINS:
        ohlcv = load_ohlcv(coin, "5m")
        if ohlcv is not None and len(ohlcv) >= 500:
            coins_ohlcv[coin.replace("-USDT", "")] = ohlcv

    # 也准备只有 close 的版本（给不需要 volume 的策略用）
    coins_close = {k: v for k, v in coins_ohlcv.items()}

    candidates = []

    # === 锚点：trend_follow（baseline）===
    tf = ShortTrendFollowStrategy()
    candidates.append(("ANCHOR:trend_follow", tf,
        {"fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        False))

    # === 锚点：swing_trail（baseline）===
    st = ShortSwingTrailStrategy()
    candidates.append(("ANCHOR:swing_trail", st,
        {"trend_ma": 180, "rsi_entry": 55, "min_gap": 288, "stop_pct": 2.5, "trail_pct": 1.5, "min_profit": 2.0},
        False))

    # === 新方向1: vol_atr ===
    va = ShortVolATRStrategy()
    va_params_list = [
        # 模仿 session 成功模式：大gap + 大MA + 宽止损
        {"vol_ma_period": 60, "vol_spike_mult": 2.5, "atr_period": 14, "atr_expand_mult": 1.5, "trend_ma": 180, "min_gap": 288, "stop_pct": 3.0, "trail_pct": 1.0},
        {"vol_ma_period": 48, "vol_spike_mult": 2.0, "atr_period": 14, "atr_expand_mult": 1.3, "trend_ma": 180, "min_gap": 288, "stop_pct": 3.0, "trail_pct": 1.0},
        {"vol_ma_period": 60, "vol_spike_mult": 3.0, "atr_period": 20, "atr_expand_mult": 1.5, "trend_ma": 180, "min_gap": 336, "stop_pct": 3.0, "trail_pct": 1.0},
        {"vol_ma_period": 48, "vol_spike_mult": 2.5, "atr_period": 14, "atr_expand_mult": 2.0, "trend_ma": 180, "min_gap": 288, "stop_pct": 2.5, "trail_pct": 1.0},
        {"vol_ma_period": 60, "vol_spike_mult": 2.0, "atr_period": 14, "atr_expand_mult": 1.5, "trend_ma": 180, "min_gap": 288, "stop_pct": 3.0, "trail_pct": 1.5, "atr_contract_exit": False},
        # 随机
        {
            "vol_ma_period": int(np.random.choice([36, 48, 60, 72])),
            "vol_spike_mult": float(np.random.choice([1.5, 2.0, 2.5, 3.0])),
            "atr_period": int(np.random.choice([10, 14, 20])),
            "atr_expand_mult": float(np.random.choice([1.3, 1.5, 1.8, 2.0])),
            "trend_ma": int(np.random.choice([96, 120, 144, 180])),
            "min_gap": int(np.random.choice([144, 192, 240, 288])),
            "stop_pct": float(np.random.choice([2.0, 2.5, 3.0])),
            "trail_pct": float(np.random.choice([1.0, 1.5, 2.0])),
        },
        {
            "vol_ma_period": int(np.random.choice([36, 48, 60, 72])),
            "vol_spike_mult": float(np.random.choice([1.5, 2.0, 2.5, 3.0])),
            "atr_period": int(np.random.choice([10, 14, 20])),
            "atr_expand_mult": float(np.random.choice([1.3, 1.5, 1.8, 2.0])),
            "trend_ma": int(np.random.choice([96, 120, 144, 180])),
            "min_gap": int(np.random.choice([144, 192, 240, 288])),
            "stop_pct": float(np.random.choice([2.0, 2.5, 3.0])),
            "trail_pct": float(np.random.choice([1.0, 1.5, 2.0])),
        },
    ]
    for params in va_params_list:
        candidates.append(("vol_atr", va, params, True))

    # === 新方向2: session 时段过滤 ===
    sf = ShortSessionFilterStrategy()
    session_params_list = [
        # 亚洲时段 (UTC 0-8)
        {"session_start": 0, "session_end": 8, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 亚洲+欧洲早盘 (UTC 0-12)
        {"session_start": 0, "session_end": 12, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 美盘 (UTC 13-21)
        {"session_start": 13, "session_end": 21, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 亚洲窄时段 (UTC 1-6)
        {"session_start": 1, "session_end": 6, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 欧洲时段 (UTC 7-15)
        {"session_start": 7, "session_end": 15, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 排除美盘（UTC 21-13 = 晚间+亚洲+欧洲早盘）— 上轮冠军
        {"session_start": 21, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # === 新冠军: stop=3.5 出场优化 ===
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.5, "take_profit_pct": 12.0, "trail_pct": 1.0},
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.5, "take_profit_pct": 8.0, "trail_pct": 1.0},
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.5, "take_profit_pct": 10.0, "trail_pct": 0.8},
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.5, "take_profit_pct": 12.0, "trail_pct": 0.8},
        # === 新冠军: stop=7.0(安全网止损) ===
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 7.0, "take_profit_pct": 12.0, "trail_pct": 1.0},
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 8.0, "take_profit_pct": 12.0, "trail_pct": 1.0},
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 7.0, "take_profit_pct": 10.0, "trail_pct": 0.8},
        # 冠军附近微调
        {"session_start": 21, "session_end": 13, "fast_ma": 96, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.5, "take_profit_pct": 12.0, "trail_pct": 1.0},
        {"session_start": 21, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 336, "stop_pct": 3.5, "take_profit_pct": 10.0, "trail_pct": 1.0},
        {"session_start": 22, "session_end": 14, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.5, "take_profit_pct": 12.0, "trail_pct": 1.0},
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 4.0, "take_profit_pct": 12.0, "trail_pct": 1.0},
        {"session_start": 20, "session_end": 13, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        {"session_start": 22, "session_end": 14, "fast_ma": 84, "slow_ma": 180, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 随机 session 变体
        {
            "session_start": int(np.random.choice([19, 20, 21, 22])),
            "session_end": int(np.random.choice([12, 13, 14, 15])),
            "fast_ma": int(np.random.choice([72, 84, 96])),
            "slow_ma": int(np.random.choice([160, 180, 200])),
            "min_gap": int(np.random.choice([240, 288, 336])),
            "stop_pct": float(np.random.choice([2.5, 3.0, 3.5])),
            "take_profit_pct": float(np.random.choice([8.0, 10.0, 12.0])),
            "trail_pct": float(np.random.choice([0.8, 1.0, 1.2])),
        },
    ]
    for params in session_params_list:
        candidates.append(("session", sf, params, False))

    # === 新方向3: multi_tf 多时间框架 ===
    mtf = ShortMultiTFStrategy()
    # 需要把 4h 数据也传进去
    # multi_tf 的 generate_signals 可以接受 price_4h 参数
    # 但 test_strategy_full 目前不支持 —— 我们用 5m 长 MA 替代（策略内部有 fallback）
    mtf_params_list = [
        # 三层全开：长MA替代4h + trend_follow + session
        {"fast_ma": 84, "slow_ma": 180, "use_session": True, "session_start": 20, "session_end": 13, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 不用 session（纯双时间框架）
        {"fast_ma": 84, "slow_ma": 180, "use_session": False, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 调整 htf MA
        {"htf_ma": 30, "fast_ma": 84, "slow_ma": 180, "use_session": True, "session_start": 20, "session_end": 13, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        {"htf_ma": 80, "fast_ma": 84, "slow_ma": 180, "use_session": True, "session_start": 20, "session_end": 13, "min_gap": 288, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 1.0},
        # 不同 5m 参数
        {"fast_ma": 96, "slow_ma": 180, "use_session": True, "session_start": 20, "session_end": 13, "min_gap": 336, "stop_pct": 3.0, "take_profit_pct": 10.0, "trail_pct": 0.8},
        # 随机
        {
            "htf_ma": int(np.random.choice([30, 50, 80])),
            "fast_ma": int(np.random.choice([72, 84, 96])),
            "slow_ma": int(np.random.choice([160, 180, 200])),
            "use_session": True,
            "session_start": int(np.random.choice([19, 20, 21])),
            "session_end": int(np.random.choice([12, 13, 14])),
            "min_gap": int(np.random.choice([240, 288, 336])),
            "stop_pct": float(np.random.choice([2.5, 3.0, 3.5])),
            "take_profit_pct": float(np.random.choice([8.0, 10.0, 12.0])),
            "trail_pct": float(np.random.choice([0.8, 1.0, 1.2])),
        },
    ]
    for params in mtf_params_list:
        candidates.append(("multi_tf", mtf, params, False))

    # === 运行所有测试 ===
    results = []
    for name, strat, params, use_vol in candidates:
        r = test_strategy_full(strat, params, coins_ohlcv, engine, use_volume=use_vol)
        r["strategy"] = name
        r["params"] = params
        results.append(r)

    results.sort(key=lambda x: x["avg_sharpe"], reverse=True)

    # === 输出 ===
    print("=" * 100)
    print("  做空策略 V2 — 新因子维度探索 vs 锚点策略")
    print("=" * 100)
    print(
        f"  {'排名':>4} | {'策略':<24} | {'Avg夏普':>8} | {'Avg收益%':>9} | "
        f"{'正收益段':>8} | {'交易数':>6} | 关键参数"
    )
    print(f"  {'─' * 92}")

    for rank, r in enumerate(results, 1):
        pos = f"{r['positive_segs']}/{r['total_segs']}"
        p = r["params"]
        name = r["strategy"]

        if "ANCHOR" in name:
            key = "(锚点 baseline)"
        elif name == "vol_atr":
            key = f"vol={p.get('vol_spike_mult','?')}x atr={p.get('atr_expand_mult','?')}x ma={p.get('trend_ma','')} gap={p['min_gap']}"
        elif name == "session":
            key = f"UTC{p['session_start']}-{p['session_end']} gap={p['min_gap']}"
        elif name == "multi_tf":
            sess = f"UTC{p.get('session_start','?')}-{p.get('session_end','?')}" if p.get("use_session") else "noSess"
            key = f"htf={p.get('htf_ma',50)} {sess} gap={p['min_gap']}"
        else:
            key = str(p)[:50]

        marker = " <<<" if "ANCHOR" in name else (" NEW!" if r["avg_sharpe"] > 2.0 else "")
        print(
            f"  {rank:>4} | {name:<24} | {r['avg_sharpe']:>+8.3f} | "
            f"{r['avg_return']:>+9.2f} | {pos:>8} | {r['total_trades']:>6} | {key}{marker}"
        )

    # === 币种优势对比 ===
    print(f"\n  {'─' * 92}")
    print(f"  新策略 vs 锚点 — 币种优势对比")
    print(f"  {'─' * 92}")

    anchor_tf = next(r for r in results if r["strategy"] == "ANCHOR:trend_follow")
    for r in results:
        if "ANCHOR" in r["strategy"]:
            continue
        # 找比锚点好的币种
        wins = []
        for coin, cd in r.get("per_coin", {}).items():
            anchor_coin = anchor_tf.get("per_coin", {}).get(coin, {})
            anchor_sharpe = anchor_coin.get("avg_sharpe", 0)
            if cd["avg_sharpe"] > anchor_sharpe and cd["avg_sharpe"] > 1.0:
                wins.append(f"{coin}({cd['avg_sharpe']:+.1f} vs {anchor_sharpe:+.1f})")
        if wins:
            print(f"  {r['strategy']:<24} 优于锚点的币种: {', '.join(wins)}")

    print(f"\n{'=' * 100}")

    # === 保存迭代结果 ===
    results_file = Path("data/short_v2_iterations.jsonl")
    results_file.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": datetime.now().isoformat(),
        "top5": [
            {
                "rank": i + 1,
                "strategy": r["strategy"],
                "avg_sharpe": round(r["avg_sharpe"], 4),
                "avg_return": round(r["avg_return"], 3),
                "positive_segs": r["positive_segs"],
                "total_segs": r["total_segs"],
                "total_trades": r["total_trades"],
                "params": r["params"],
                "per_coin_sharpe": {
                    coin: round(cd["avg_sharpe"], 2)
                    for coin, cd in r.get("per_coin", {}).items()
                    if cd["avg_sharpe"] > 1.0
                },
            }
            for i, r in enumerate(results[:5])
        ],
        "anchor_sharpe": anchor_tf["avg_sharpe"],
    }
    with open(results_file, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")

    # 读取历史，显示趋势
    lines = results_file.read_text().strip().split("\n")
    n_iters = len(lines)
    print(f"\n  迭代历史 ({n_iters} 轮):")
    for i, line in enumerate(lines[-5:], max(1, n_iters - 4)):
        rec = json.loads(line)
        top = rec["top5"][0]
        anchor = rec.get("anchor_sharpe", 0)
        delta = top["avg_sharpe"] - anchor
        print(
            f"    #{i:>3} | {rec['timestamp'][:19]} | "
            f"冠军: {top['strategy']:<20} Sharpe={top['avg_sharpe']:>+.3f} "
            f"(vs 锚点 {delta:>+.3f})"
        )

    # 历史最优
    all_records = [json.loads(l) for l in lines]
    best_ever = max(all_records, key=lambda x: x["top5"][0]["avg_sharpe"])
    best_top = best_ever["top5"][0]
    print(
        f"\n  历史最优: {best_top['strategy']} | Sharpe={best_top['avg_sharpe']:+.3f} | "
        f"{best_ever['timestamp'][:19]}"
    )
    if best_top.get("per_coin_sharpe"):
        coins_str = " ".join(f"{c}:{s:+.1f}" for c, s in sorted(best_top["per_coin_sharpe"].items(), key=lambda x: -x[1])[:5])
        print(f"  币种优势: {coins_str}")


if __name__ == "__main__":
    main()
