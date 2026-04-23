"""在大事件时段测试 ROBUST 策略的表现。"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.costs import OKX_SPOT
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.storage.parquet_writer import ParquetWriter
from src.analysis.market_events import MarketEventDB
from config.settings import get_settings

from src.strategies.ichimoku_momentum import ichimoku_momentum_signal
from src.strategies.aggressive_momentum import aggressive_momentum_signal
from src.strategies.trend_ma_filtered import trend_ma_filtered_signal

STRATEGIES = {
    "IchiMom_v2": (ichimoku_momentum_signal, {"tenkan": 9, "kijun": 26, "lookback": 50, "consec_bars": 4}),
    "AggrMom": (aggressive_momentum_signal, {"lookback": 50, "consec_bars": 4, "trail_atr_mult": 1.5}),
    "TrendMA_Filt": (trend_ma_filtered_signal, {"short_window": 25, "long_window": 200, "atr_mult": 0.5}),
}


def load_price(symbol: str) -> pd.Series | None:
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, "4h")
    if df.is_empty():
        return None
    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    return pdf.set_index("datetime").sort_index()["close"]


def main() -> None:
    settings = get_settings()
    event_db = MarketEventDB(settings.sqlite_path)
    engine = BacktestEngine(costs=OKX_SPOT, init_cash=100_000, freq="4h")

    events = event_db.get_events()
    logger.info(f"已加载 {len(events)} 个市场事件")

    all_rows = []

    for sym in ["BTC-USDT", "ETH-USDT"]:
        price = load_price(sym)
        if price is None:
            continue

        windows = event_db.get_event_windows(price, days_before=7, days_after=30)
        logger.info(f"\n{'='*80}")
        logger.info(f"{sym} | {len(windows)} 个事件有足够数据")
        logger.info(f"{'='*80}")

        for w in windows:
            window_price = w["price_window"]
            if len(window_price) < 30:
                continue

            for sname, (func, params) in STRATEGIES.items():
                try:
                    e, x = func(window_price, **params)
                    pf = engine.run(window_price, e, x)
                    m = compute_metrics(pf)
                except Exception:
                    m = {"sharpe_ratio": 0, "total_return_pct": 0, "max_drawdown_pct": 0, "total_trades": 0}

                all_rows.append({
                    "symbol": sym,
                    "event": w["title"],
                    "event_type": w["type"],
                    "impact": w["impact"],
                    "date": w["date"],
                    "strategy": sname,
                    **m,
                })

            # 打印该事件下最优策略
            event_results = [r for r in all_rows if r["event"] == w["title"] and r["symbol"] == sym]
            best = max(event_results, key=lambda x: x.get("sharpe_ratio", 0))
            price_change = (window_price.iloc[-1] - window_price.iloc[0]) / window_price.iloc[0] * 100

            logger.info(
                f"  [{w['date']}] {w['title'][:30]:30s} | "
                f"市场:{price_change:+.1f}% | "
                f"最优:{best['strategy']:12s} sharpe:{best.get('sharpe_ratio',0):+.3f}"
            )

    df = pd.DataFrame(all_rows)

    # 按事件类型汇总
    logger.info(f"\n{'='*80}")
    logger.info("按事件类型 x 策略表现")
    logger.info(f"{'='*80}")

    for etype in ["ETF", "MACRO", "REGULATION", "PROTOCOL", "PRICE"]:
        subset = df[df["event_type"] == etype]
        if subset.empty:
            continue
        logger.info(f"\n  --- {etype} 事件 ---")
        for sname in STRATEGIES:
            s = subset[subset["strategy"] == sname]
            if s.empty:
                continue
            avg = s["sharpe_ratio"].mean()
            pos = (s["sharpe_ratio"] > 0).mean() * 100
            logger.info(f"    {sname:15s} | avg_sharpe:{avg:+.3f} | positive:{pos:4.0f}%")

    # 按 bullish/bearish 汇总
    logger.info(f"\n{'='*80}")
    logger.info("利好 vs 利空事件")
    logger.info(f"{'='*80}")
    for impact in ["bullish", "bearish"]:
        subset = df[df["impact"] == impact]
        if subset.empty:
            continue
        logger.info(f"\n  --- {impact.upper()} ---")
        for sname in STRATEGIES:
            s = subset[subset["strategy"] == sname]
            if s.empty:
                continue
            avg = s["sharpe_ratio"].mean()
            logger.info(f"    {sname:15s} | avg_sharpe:{avg:+.3f}")

    output = Path("reports") / "event_backtest.csv"
    df.to_csv(output, index=False)
    logger.info(f"\n保存到: {output}")

    event_db.close()
    logger.success("事件回测完成！")


if __name__ == "__main__":
    main()
