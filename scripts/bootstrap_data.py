"""初始化数据回填：拉取 BTC/ETH 的 1h, 4h, 1d K线（过去2年）+ 资金费率。"""

import asyncio
import sys
import time
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.exchange.okx_client import OKXNativeClient
from src.ingestion.funding import FundingIngestor
from src.ingestion.ohlcv import OHLCVIngestor
from src.storage.parquet_writer import ParquetWriter
from src.storage.state_tracker import StateTracker

console = Console()

# 回填配置
SYMBOLS = ["BTC-USDT", "ETH-USDT"]
TIMEFRAMES = ["1h", "4h", "1d"]
LOOKBACK_DAYS = 730  # 2年
SWAP_SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
FUNDING_LOOKBACK_DAYS = 90  # 资金费率最多3个月


async def bootstrap_ohlcv(
    ccxt_client: CCXTClient,
    writer: ParquetWriter,
    state_tracker: StateTracker,
) -> None:
    """回填 OHLCV 数据。"""
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - LOOKBACK_DAYS * 24 * 3600 * 1000

    ingestor = OHLCVIngestor(ccxt_client, writer, state_tracker, market_type="spot")

    total_tasks = len(SYMBOLS) * len(TIMEFRAMES)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("OHLCV 回填", total=total_tasks)

        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                progress.update(task, description=f"OHLCV {symbol} {tf}")

                # 检查是否有存量数据（增量模式）
                last_ts = state_tracker.get_last_timestamp("ohlcv", symbol, tf)
                actual_since = last_ts + 1 if last_ts else since_ms

                logger.info(f"开始回填 | {symbol} {tf} | since: {actual_since}")

                raw = await ingestor.fetch(symbol, tf, since=actual_since)
                df = ingestor.transform(raw, symbol)

                if not df.is_empty():
                    written = ingestor.save(df, symbol, tf)
                    max_ts = df["timestamp"].max()
                    state_tracker.update_last_timestamp("ohlcv", symbol, tf, max_ts)
                    logger.info(f"回填完成 | {symbol} {tf} | 写入 {written} 行")
                else:
                    logger.info(f"无新数据 | {symbol} {tf}")

                progress.advance(task)


async def bootstrap_funding(
    okx_client: OKXNativeClient,
    writer: ParquetWriter,
    state_tracker: StateTracker,
) -> None:
    """回填资金费率数据。"""
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - FUNDING_LOOKBACK_DAYS * 24 * 3600 * 1000

    ingestor = FundingIngestor(okx_client, writer, state_tracker)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Funding 回填", total=len(SWAP_SYMBOLS))

        for symbol in SWAP_SYMBOLS:
            progress.update(task, description=f"Funding {symbol}")

            last_ts = state_tracker.get_last_timestamp("funding", symbol, "")
            actual_since = last_ts + 1 if last_ts else since_ms

            raw = await ingestor.fetch(symbol, "", since=actual_since)
            df = ingestor.transform(raw, symbol)

            if not df.is_empty():
                written = ingestor.save(df, symbol, "")
                max_ts = df["timestamp"].max()
                state_tracker.update_last_timestamp("funding", symbol, "", max_ts)
                logger.info(f"回填完成 | {symbol} | 写入 {written} 行")
            else:
                logger.info(f"无新数据 | {symbol}")

            progress.advance(task)


async def main() -> None:
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("数据回填开始")
    logger.info(f"OHLCV: {SYMBOLS} x {TIMEFRAMES} x {LOOKBACK_DAYS}天")
    logger.info(f"Funding: {SWAP_SYMBOLS} x {FUNDING_LOOKBACK_DAYS}天")
    logger.info("=" * 60)

    writer = ParquetWriter(settings.parquet_dir)
    state_tracker = StateTracker(settings.sqlite_path)

    # OHLCV
    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
        use_simulated=settings.okx_use_simulated,
    ) as ccxt_client:
        await bootstrap_ohlcv(ccxt_client, writer, state_tracker)

    # Funding
    okx_client = OKXNativeClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
        use_simulated=settings.okx_use_simulated,
    )
    await bootstrap_funding(okx_client, writer, state_tracker)

    state_tracker.close()

    logger.success("数据回填完成！")

    # 验证数据
    console.print("\n[bold green]数据验证:[/bold green]")
    from src.storage.duckdb_client import DuckDBClient

    with DuckDBClient(settings.duckdb_path, settings.parquet_dir) as db:
        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                try:
                    pattern = str(settings.parquet_dir / "ohlcv" / "spot" / symbol / tf / "*.parquet")
                    result = db.query_df(
                        f"SELECT COUNT(*) as cnt, MIN(timestamp) as min_ts, MAX(timestamp) as max_ts FROM read_parquet('{pattern}')"
                    )
                    if not result.is_empty():
                        row = result.row(0)
                        console.print(f"  {symbol} {tf}: {row[0]} 行 | {row[1]} ~ {row[2]}")
                except Exception as e:
                    console.print(f"  {symbol} {tf}: 无数据 ({e})")


if __name__ == "__main__":
    asyncio.run(main())
