"""前向 runner 价源预热 CLI:确保 followed 标的在目标交易日有日收盘价(我方缓存/prices.db,缺则 yfinance 补)。

新鲜度机制(给 Exec 的确定答案):**本命令是日收盘新鲜度的负责方,不依赖 stock-picker 的 cron。**
前向 runner 每个交易日盘后(美东 16:00 收盘 + ≥1.5h,建议 17:30 ET 后,yfinance 当日官方 close 已落)
先跑本命令预热,再撮合;按返回的 missing 列表**逐票** skip(no_price),而非整轮停。

用法:
    uv run python -m src.data.warm_prices --date 2026-06-10                  # 票取自 signal_candidates
    uv run python -m src.data.warm_prices --date 2026-06-10 --tickers NVDA,AMD
退出码:0=全部覆盖或已 yfinance 补齐;2=有 missing(yfinance 也拿不到,runner 据此逐票 skip);
        3=yfinance 整体不可用(全 missing,疑似网络/限速,runner 应停并告警)。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import duckdb
from loguru import logger

from src.data.price_source import PriceSource
from src.signals.tweet_snapshot import DEFAULT_SNAPSHOT_DB


def _candidate_tickers() -> list[str]:
    """从 signal_candidates 读 distinct followed 标的(只读)。"""
    if not DEFAULT_SNAPSHOT_DB.exists():
        return []
    con = duckdb.connect(str(DEFAULT_SNAPSHOT_DB), read_only=True)
    try:
        return sorted(r[0] for r in con.execute("SELECT DISTINCT ticker FROM signal_candidates").fetchall())
    except duckdb.CatalogException:
        return []
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    """预热目标交易日的 followed 标的日收盘价,返回退出码。"""
    parser = argparse.ArgumentParser(description="前向 runner 价源预热(日收盘)")
    parser.add_argument("--date", required=True, help="目标交易日 YYYY-MM-DD")
    parser.add_argument("--tickers", default="", help="逗号分隔;省略则取自 signal_candidates")
    args = parser.parse_args(argv)

    target = date.fromisoformat(args.date)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or _candidate_tickers()
    if not tickers:
        logger.error("无标的可预热(--tickers 空且 signal_candidates 无数据)")
        return 3

    rep = PriceSource().warm_daily_close(tickers, target)
    logger.info("预热 {} 完成: {} 票 → {}", target, len(tickers), rep)
    if rep["fetched"] == 0 and rep["covered"] == 0 and rep["missing"] == len(tickers):
        logger.error("全 {} 票均无价 → 疑似 yfinance 整体不可用,runner 应停并告警", rep["missing"])
        return 3
    return 2 if rep["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
