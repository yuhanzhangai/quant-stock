"""拉取 altcoin 5m 数据 (3个月) 并用 MinSwing 做三段验证。

币种: AAVE, UNI, LDO, MKR, CRV, RENDER, INJ, TIA
策略: MinSwing(trend_ma=180, stop_pct=2.0, take_profit_pct=8.0, min_gap=144)
费率: OKX_SWAP, init_cash=250
"""

import asyncio
import sys
import time
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.ingestion.ohlcv import OHLCVIngestor
from src.storage.parquet_writer import ParquetWriter
from src.storage.state_tracker import StateTracker
from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.strategies.minute_swing import minute_swing_signal

# ── 配置 ──────────────────────────────────────────
COINS = [
    "AAVE-USDT", "UNI-USDT", "LDO-USDT", "MKR-USDT",
    "CRV-USDT", "RENDER-USDT", "INJ-USDT", "TIA-USDT",
]
TIMEFRAME = "5m"
LOOKBACK_DAYS = 90  # 3个月

# MinSwing 参数
MS_PARAMS = dict(
    trend_ma=180,
    stop_pct=2.0,
    take_profit_pct=8.0,
    min_gap=144,
)

INIT_CASH = 250.0


# ── 数据拉取 ─────────────────────────────────────
async def fetch_all_coins() -> None:
    """拉取所有 altcoin 的 5m 数据 (3个月)。"""
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    state_tracker = StateTracker(settings.sqlite_path)

    now_ms = int(time.time() * 1000)
    since_ms = now_ms - LOOKBACK_DAYS * 24 * 3600 * 1000

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
        use_simulated=settings.okx_use_simulated,
    ) as ccxt_client:
        ingestor = OHLCVIngestor(ccxt_client, writer, state_tracker, market_type="spot")

        for symbol in COINS:
            print(f"  拉取 {symbol} {TIMEFRAME} ...")
            try:
                last_ts = state_tracker.get_last_timestamp("ohlcv", symbol, TIMEFRAME)
                actual_since = last_ts + 1 if last_ts else since_ms

                raw = await ingestor.fetch(symbol, TIMEFRAME, since=actual_since)
                df = ingestor.transform(raw, symbol)

                if not df.is_empty():
                    written = ingestor.save(df, symbol, TIMEFRAME)
                    max_ts = df["timestamp"].max()
                    state_tracker.update_last_timestamp("ohlcv", symbol, TIMEFRAME, max_ts)
                    print(f"    -> {symbol}: 写入 {written} 行 (总 {len(df)} 行)")
                else:
                    print(f"    -> {symbol}: 无新数据")
            except Exception as e:
                print(f"    -> {symbol}: 拉取失败 - {e}")

    state_tracker.close()


# ── 回测 ──────────────────────────────────────────
def load_price(symbol: str) -> pd.Series | None:
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, TIMEFRAME)
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


def split_3_segments(price: pd.Series) -> list[tuple[str, pd.Series]]:
    n = len(price)
    seg_len = n // 3
    segments = []
    for i, label in enumerate(["seg1", "seg2", "seg3"]):
        start = i * seg_len
        end = (i + 1) * seg_len if i < 2 else n
        segments.append((label, price.iloc[start:end]))
    return segments


def run_backtest() -> None:
    engine = BacktestEngine(costs=OKX_SWAP, init_cash=INIT_CASH, freq="5min")

    # 收集结果
    results = []  # (coin, avg_sharpe, sharpes, details)

    print("\n" + "=" * 90)
    print("  MinSwing 三段验证  |  OKX_SWAP 费率  |  init_cash=250")
    print(f"  参数: trend_ma={MS_PARAMS['trend_ma']}  stop={MS_PARAMS['stop_pct']}%"
          f"  tp={MS_PARAMS['take_profit_pct']}%  min_gap={MS_PARAMS['min_gap']}")
    print("=" * 90)

    for coin in COINS:
        price = load_price(coin)
        if price is None or len(price) < 500:
            print(f"\n{coin}: 数据不足 ({0 if price is None else len(price)} 根), 跳过")
            continue

        print(f"\n{'─' * 85}")
        print(f"  {coin}  |  总: {len(price)} 根 5m K线  |  {price.index[0]} ~ {price.index[-1]}")
        print(f"{'─' * 85}")
        print(f"  {'段':>6} | {'入场':>4} | {'出场':>4} | {'收益%':>8} | {'Sharpe':>7} | {'MaxDD%':>7} | {'胜率%':>6} | {'终值':>10}")
        print(f"  {'─' * 80}")

        segments = split_3_segments(price)
        sharpes = []
        seg_details = []

        for seg_label, seg_price in segments:
            if len(seg_price) < 300:
                print(f"  {seg_label:>6} | 数据不足 ({len(seg_price)} 根)")
                sharpes.append(0.0)
                continue

            entries, exits = minute_swing_signal(seg_price, **MS_PARAMS)
            n_entries = int(entries.sum())
            n_exits = int(exits.sum())

            if n_entries == 0:
                print(f"  {seg_label:>6} | {n_entries:>4} | {n_exits:>4} |   无交易")
                sharpes.append(0.0)
                continue

            pf = engine.run(seg_price, entries, exits)
            m = compute_metrics(pf)

            total_ret = m.get("total_return_pct", 0.0)
            sharpe = m.get("sharpe_ratio", 0.0)
            max_dd = m.get("max_drawdown_pct", 0.0)
            win_rate = m.get("win_rate_pct", 0.0)
            final_val = m.get("final_value", INIT_CASH)

            sharpes.append(sharpe)
            seg_details.append(m)

            print(
                f"  {seg_label:>6} | {n_entries:>4} | {n_exits:>4} | "
                f"{total_ret:>+8.2f} | {sharpe:>7.2f} | {max_dd:>7.2f} | "
                f"{win_rate:>6.1f} | {final_val:>10.2f}"
            )

        avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0.0
        all_positive = all(s > 0 for s in sharpes) and len(sharpes) == 3
        results.append((coin, avg_sharpe, sharpes, all_positive))

    # ── 排名 ──────────────────────────────────────
    results.sort(key=lambda x: x[1], reverse=True)

    print("\n" + "=" * 90)
    print("  排名 (按 avg Sharpe 降序)")
    print("=" * 90)
    print(f"  {'#':>3} | {'币种':<14} | {'Avg Sharpe':>10} | {'seg1':>7} | {'seg2':>7} | {'seg3':>7} | {'状态':<10}")
    print(f"  {'─' * 75}")

    for rank, (coin, avg_s, sharpes, all_pos) in enumerate(results, 1):
        s1 = sharpes[0] if len(sharpes) > 0 else 0.0
        s2 = sharpes[1] if len(sharpes) > 1 else 0.0
        s3 = sharpes[2] if len(sharpes) > 2 else 0.0
        tag = "*** 3/3 ***" if all_pos else ""
        print(
            f"  {rank:>3} | {coin:<14} | {avg_s:>10.3f} | {s1:>7.2f} | {s2:>7.2f} | {s3:>7.2f} | {tag}"
        )

    # 统计
    n_all_pos = sum(1 for _, _, _, ap in results if ap)
    print(f"\n  共 {len(results)} 币种, 其中 {n_all_pos} 个 3/3 全正 Sharpe")
    print("=" * 90)


# ── Main ──────────────────────────────────────────
async def main() -> None:
    print("=" * 90)
    print("  Step 1: 拉取 altcoin 5m 数据 (3个月)")
    print("=" * 90)
    await fetch_all_coins()

    print("\n\n")
    print("=" * 90)
    print("  Step 2: MinSwing 三段验证")
    print("=" * 90)
    run_backtest()


if __name__ == "__main__":
    asyncio.run(main())
