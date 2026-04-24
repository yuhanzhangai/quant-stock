"""数据质量检查脚本。

Usage:
    python scripts/run_data_quality.py --symbol ETH-USDT --timeframe 5m
    python scripts/run_data_quality.py --all
    python scripts/run_data_quality.py --all --save-db
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse

from loguru import logger

from config.settings import get_settings
from src.data_quality.checks import has_critical_failure, run_all_checks
from src.data_quality.report import save_report_json, save_to_db
from src.storage.parquet_writer import ParquetWriter


def check_single(symbol: str, timeframe: str, writer: ParquetWriter, save_db: bool = False) -> bool:
    """检查单个 symbol/timeframe 组合。返回 True 如果通过。"""
    logger.info(f"Checking {symbol} / {timeframe}")

    df = writer.read_ohlcv(symbol, timeframe)
    if df.is_empty():
        logger.warning(f"  No data for {symbol}/{timeframe}")
        return True  # No data = no issues

    results = run_all_checks(df, timeframe=timeframe)

    # Save report
    report_path = save_report_json(results, symbol, timeframe)
    logger.info(f"  Report: {report_path}")

    if save_db:
        # Read data version
        version_file = Path("data/meta/latest_data_version.txt")
        data_version = version_file.read_text().strip() if version_file.exists() else ""
        save_to_db(results, symbol, timeframe, data_version=data_version)

    if has_critical_failure(results):
        logger.error(f"  CRITICAL FAILURE for {symbol}/{timeframe}")
        return False

    return True


def check_all(writer: ParquetWriter, save_db: bool = False) -> tuple[int, int]:
    """检查所有可用数据。返回 (passed, failed) 计数。"""
    settings = get_settings()
    ohlcv_dir = settings.parquet_dir / "ohlcv" / "spot"

    if not ohlcv_dir.exists():
        logger.warning("No OHLCV data directory")
        return 0, 0

    passed = 0
    failed = 0

    for symbol_dir in sorted(ohlcv_dir.iterdir()):
        if not symbol_dir.is_dir():
            continue
        symbol = symbol_dir.name

        for tf_dir in sorted(symbol_dir.iterdir()):
            if not tf_dir.is_dir():
                continue
            timeframe = tf_dir.name

            ok = check_single(symbol, timeframe, writer, save_db)
            if ok:
                passed += 1
            else:
                failed += 1

    return passed, failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run data quality checks")
    parser.add_argument("--symbol", type=str, help="Symbol to check (e.g., ETH-USDT)")
    parser.add_argument("--timeframe", type=str, default="5m", help="Timeframe (default: 5m)")
    parser.add_argument("--all", action="store_true", help="Check all symbols/timeframes")
    parser.add_argument("--save-db", action="store_true", help="Save results to research.duckdb")
    args = parser.parse_args()

    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)

    if args.all:
        passed, failed = check_all(writer, save_db=args.save_db)
        logger.info(f"=== Data Quality Summary: {passed} passed, {failed} failed ===")
        if failed > 0:
            sys.exit(1)
    elif args.symbol:
        ok = check_single(args.symbol, args.timeframe, writer, save_db=args.save_db)
        if not ok:
            sys.exit(1)
    else:
        parser.print_help()
