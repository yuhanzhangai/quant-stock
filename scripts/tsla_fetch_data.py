"""拉取 TSLA-USDT-SWAP 历史 K 线数据。

从 OKX 获取特斯拉永续合约的历史数据，存储为 Parquet。
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.exchange.ccxt_client import CCXTClient


async def fetch_tsla_ohlcv(
    timeframe: str = "1h",
    days_back: int = 365,
) -> pd.DataFrame:
    """拉取 TSLA-USDT-SWAP K 线数据。

    Args:
        timeframe: K 线周期，如 "1h", "15m", "4h"
        days_back: 向前拉取天数

    Returns:
        OHLCV DataFrame
    """
    # TSLA-USDT-SWAP 创建于 2026-02-25，从创建时间开始拉取
    tsla_created_ts = 1772010000000  # 2026-02-25 09:00 UTC
    end_ts = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    start_ts = max(tsla_created_ts, end_ts - days_back * 24 * 3600 * 1000)

    logger.info(
        f"开始拉取 TSLA-USDT-SWAP | {timeframe} | "
        f"从 {datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')} "
        f"到 {datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')}"
    )

    # CCXT 中永续合约格式为 TSLA/USDT:USDT
    symbol = "TSLA/USDT:USDT"

    async with CCXTClient() as client:
        raw = await client.fetch_ohlcv_range(
            symbol=symbol,
            timeframe=timeframe,
            since=start_ts,
            end=end_ts,
        )

    if not raw:
        logger.error("未获取到任何数据！请检查 TSLA-USDT-SWAP 是否可用")
        return pd.DataFrame()

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]

    logger.info(
        f"拉取完成 | {len(df)} 根K线 | "
        f"范围: {df.index[0]} ~ {df.index[-1]} | "
        f"价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}"
    )
    return df


def save_tsla_data(df: pd.DataFrame, timeframe: str = "1h") -> Path:
    """保存 TSLA 数据到 Parquet。

    Args:
        df: OHLCV DataFrame
        timeframe: 时间周期

    Returns:
        保存路径
    """
    out_dir = Path("data/parquet/ohlcv/swap/TSLA-USDT")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{timeframe}.parquet"

    df.to_parquet(out_path)
    logger.info(f"已保存 → {out_path} ({len(df)} 行)")
    return out_path


async def main() -> None:
    """主入口。"""
    # 拉取 1h 和 15m 两个周期
    for tf in ["1h", "15m"]:
        df = await fetch_tsla_ohlcv(timeframe=tf, days_back=365)
        if not df.empty:
            save_tsla_data(df, timeframe=tf)


if __name__ == "__main__":
    asyncio.run(main())
