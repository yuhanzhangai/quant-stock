"""MinSwing 1m 参数网格搜索 (ETH-USDT / SOL-USDT)。

步骤：
1. 拉取 ETH-USDT 和 SOL-USDT 的 1m 数据（2周）
2. 在 OOS 后半段做参数网格搜索
3. 打印 top 10 结果

费率: OKX_SWAP, init_cash=250, freq='1min'
"""

import asyncio
import sys
import time
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.backtest.costs import OKX_SWAP
from src.backtest.engine import BacktestEngine
from src.exchange.ccxt_client import CCXTClient
from src.ingestion.ohlcv import OHLCVIngestor
from src.storage.parquet_writer import ParquetWriter
from src.storage.state_tracker import StateTracker
from src.strategies.minute_swing import minute_swing_signal

COINS = ["ETH-USDT", "SOL-USDT"]
LOOKBACK_DAYS = 14  # 2 周

GRID = {
    "trend_ma": [600, 900, 1200],
    "stop_pct": [1.0, 1.5, 2.0],
    "take_profit_pct": [3.0, 5.0, 8.0],
    "min_gap": [360, 720, 1440],
}


# ── Step 1: 拉取 1m 数据 ─────────────────────────────────────────
async def fetch_1m_data() -> None:
    """拉取 ETH-USDT 和 SOL-USDT 的 1m K线，过去 2 周。"""
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
            logger.info(f"拉取 {symbol} 1m 数据 (过去 {LOOKBACK_DAYS} 天)...")
            raw = await ingestor.fetch(symbol, "1m", since=since_ms)
            df = ingestor.transform(raw, symbol)
            if not df.is_empty():
                written = ingestor.save(df, symbol, "1m")
                logger.info(f"{symbol} 1m 写入 {written} 行 (总 {len(df)} 根)")
            else:
                logger.warning(f"{symbol} 1m 无数据返回")

    state_tracker.close()


# ── Step 2: 加载价格 ─────────────────────────────────────────────
def load_price_1m(symbol: str) -> pd.Series | None:
    """加载 1m 收盘价。"""
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, "1m")
    if df.is_empty():
        logger.warning(f"{symbol} 1m 数据为空")
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


# ── Step 3: 网格搜索 + OOS 验证 ─────────────────────────────────
def main() -> None:
    # --- 拉数据 ---
    print("=" * 110)
    print("Step 1: 拉取 1m K线数据 (2 周)")
    print("=" * 110)
    asyncio.run(fetch_1m_data())

    # --- 参数网格 ---
    total_combos = 1
    for v in GRID.values():
        total_combos *= len(v)

    print()
    print("=" * 110)
    print("Step 2-3: MinSwing 1m 参数网格搜索 + OOS 后半段验证")
    print(f"费率: OKX_SWAP (taker={OKX_SWAP.taker_fee:.4%}, slippage={OKX_SWAP.slippage_bps}bp)")
    print("初始资金: 250 USDT | freq: 1min")
    print(f"参数组合数: {total_combos}")
    print(f"搜索范围: {GRID}")
    print("=" * 110)

    for sym in COINS:
        print(f"\n{'=' * 110}")
        print(f"  {sym} - OOS 后半段网格搜索")
        print(f"{'=' * 110}")

        price = load_price_1m(sym)
        if price is None or len(price) < 2000:
            print(f"  {sym} 数据不足 (需要至少 2000 根 1m K线), 跳过")
            continue

        # OOS 后半段
        n = len(price)
        oos_start = n // 2
        oos_price = price.iloc[oos_start:]
        print(f"  总数据量: {n} 根 1m K线")
        print(
            f"  OOS 后半段: {len(oos_price)} 根 "
            f"({oos_price.index[0].strftime('%Y-%m-%d %H:%M')} "
            f"~ {oos_price.index[-1].strftime('%Y-%m-%d %H:%M')})"
        )

        engine = BacktestEngine(costs=OKX_SWAP, init_cash=250, freq="1min")

        print(f"  开始搜索 {total_combos} 个参数组合...\n")
        results_df, best_params = engine.run_grid_search(
            price=oos_price,
            signal_func=minute_swing_signal,
            param_grid=GRID,
        )

        # 按 sharpe_ratio 排序, top 10
        results_sorted = results_df.sort_values("sharpe_ratio", ascending=False).reset_index(drop=True)

        print(f"\n  === {sym} Top 10 结果 (按 Sharpe 排序, OOS 后半段) ===")
        header = (
            f"  {'Rank':<5} {'trend_ma':>8} {'stop%':>7} {'TP%':>7} {'min_gap':>8} "
            f"{'收益%':>9} {'Sharpe':>8} {'Sortino':>8} {'MaxDD%':>8} {'胜率%':>7} {'交易数':>6} {'终值':>10}"
        )
        print(header)
        print("  " + "-" * 106)

        top10 = results_sorted.head(10)
        for rank, (_, row) in enumerate(top10.iterrows(), 1):
            print(
                f"  {rank:<5} {int(row['trend_ma']):>8} {row['stop_pct']:>7.1f} "
                f"{row['take_profit_pct']:>7.1f} {int(row['min_gap']):>8} "
                f"{row['total_return_pct']:>8.2f}% "
                f"{row['sharpe_ratio']:>8.3f} "
                f"{row['sortino_ratio']:>8.3f} "
                f"{row['max_drawdown_pct']:>7.2f}% "
                f"{row['win_rate_pct']:>6.1f}% "
                f"{int(row['total_trades']):>6} "
                f"{row['final_value']:>10.2f}"
            )

        print(f"\n  === {sym} 最优参数 ===")
        for k, v in best_params.items():
            print(f"    {k}: {v}")
        best_row = results_sorted.iloc[0]
        print(
            f"    => Sharpe: {best_row['sharpe_ratio']:.3f}, "
            f"收益: {best_row['total_return_pct']:.2f}%, "
            f"MaxDD: {best_row['max_drawdown_pct']:.2f}%"
        )

    print(f"\n{'=' * 110}")
    print("网格搜索完成。")


if __name__ == "__main__":
    main()
