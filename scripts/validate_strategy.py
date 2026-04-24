"""统一策略验证脚本。

一个命令跑完整验证流水线（9 个 gate），输出 pass/fail。

Usage:
    python scripts/validate_strategy.py --strategy minswing_v3 --symbol ETH-USDT --timeframe 5m
    python scripts/validate_strategy.py --experiment experiments/active/xxx.yml
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse

import pandas as pd
import yaml
from loguru import logger

from config.settings import get_settings
from src.storage.parquet_writer import ParquetWriter
from src.validation.runner import run_full_validation, save_validation_report


def load_strategy_func(strategy_name: str):
    """根据策略名加载信号函数。"""
    if strategy_name == "minswing_v3":
        from src.strategies.minswing_v3_final import MinSwingV3Strategy

        strat = MinSwingV3Strategy()
        return lambda price, **kw: strat.generate_signals(price, **kw)
    elif strategy_name == "minute_swing":
        from src.strategies.minute_swing import MinuteSwingStrategy

        strat = MinuteSwingStrategy()
        return lambda price, **kw: strat.generate_signals(price, **kw)
    elif strategy_name == "fast_exit":
        from src.strategies.combo.fast_exit import FastExitStrategy

        strat = FastExitStrategy()
        return lambda price, **kw: strat.generate_signals(price, **kw)
    elif strategy_name == "short_session_filter":
        from src.strategies.short.short_session_filter import ShortSessionFilterStrategy

        strat = ShortSessionFilterStrategy()
        return lambda price, **kw: strat.generate_signals(price, **kw)
    elif strategy_name == "short_trend_follow":
        from src.strategies.short.short_trend_follow import ShortTrendFollowStrategy

        strat = ShortTrendFollowStrategy()
        return lambda price, **kw: strat.generate_signals(price, **kw)
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")


def main() -> None:
    """主函数。"""
    parser = argparse.ArgumentParser(description="Validate strategy through 9-gate pipeline")
    parser.add_argument("--strategy", type=str, help="Strategy name")
    parser.add_argument("--symbol", type=str, default="ETH-USDT", help="Symbol")
    parser.add_argument("--timeframe", type=str, default="5m", help="Timeframe")
    parser.add_argument("--experiment", type=str, help="Path to experiment YAML")
    args = parser.parse_args()

    # Load from experiment file or CLI args
    if args.experiment:
        with open(args.experiment, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        strategy_name = config["strategy_name"]
        symbols = config.get("data", {}).get("symbols", ["ETH-USDT"])
        symbol = symbols[0] if symbols else "ETH-USDT"
        timeframe = config.get("data", {}).get("timeframe", "5m")
        params = config.get("params", {}).get("fixed", {})
    else:
        if not args.strategy:
            parser.print_help()
            return
        strategy_name = args.strategy
        symbol = args.symbol
        timeframe = args.timeframe
        params = {}

    logger.info(f"Validating: {strategy_name} on {symbol}/{timeframe}")

    # Load data
    settings = get_settings()
    writer = ParquetWriter(settings.parquet_dir)
    df = writer.read_ohlcv(symbol, timeframe)

    if df.is_empty():
        logger.error(f"No data for {symbol}/{timeframe}")
        sys.exit(1)

    pdf = df.to_pandas()
    pdf["datetime"] = pd.to_datetime(pdf["timestamp"], unit="ms", utc=True)
    pdf = pdf.set_index("datetime").sort_index()
    price = pdf["close"]

    # Load strategy
    signal_func = load_strategy_func(strategy_name)

    # Run validation
    results = run_full_validation(
        df=df,
        price=price,
        signal_func=signal_func,
        params=params,
        timeframe=timeframe,
    )

    # Save report
    run_id = f"val_{strategy_name}_{symbol.replace('-USDT', '').lower()}_{timeframe}"
    save_validation_report(results, run_id=run_id, strategy_name=strategy_name)

    # Exit code
    failed = sum(1 for r in results if r.status == "fail")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
