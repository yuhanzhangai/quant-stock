"""PaperBroker 权威价源:信号时点成交价 + 每日收盘价。源 = prices.db(只读日线)+ yfinance 补。

铁律(见 docs/PRICE_SOURCE_SPEC.md):
- 拿不到可信价 → 抛 PriceUnavailable,**绝不返回假价**(演练注入价范式终止)。
- prices.db 只读(mode=ro);是后向复权日线,只配近端 mark / 收益率,不当历史成交价。
- 成交价(price_at)取 yfinance raw(adjusted=False,我那刻真会付的价);每日 mark(close_on)
  prices.db 优先(已维护 1575 票省限速),adjusted 字段如实标注口径供下游(Valid)按拆股对齐。

yfinance 访问经可注入回调(quote_fn/daily_fn),默认走真 yfinance;测试注入假实现以离线跑(不打网络)。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import duckdb
from loguru import logger

from src.signals.paths import assert_writable_path, connect_readonly

# 注入点签名:
#   quote_fn(ticker) -> (price, as_of_utc) | None   近端最新成交价(raw),拿不到返回 None
#   daily_fn(ticker, start, end) -> {date: close}    [start,end] 原始日收盘(raw),空 dict=无数据
QuoteFn = Callable[[str], "tuple[float, datetime] | None"]
DailyFn = Callable[[str, date, date], "dict[date, float]"]


class PriceUnavailable(Exception):
    """拿不到可信价:ticker 无效/停牌/超出数据窗口/yfinance 失败。绝不返回假价。"""


@dataclass(frozen=True)
class PricePoint:
    """一个价点 + 其溯源元数据(审计成交价偏差用)。"""

    ticker: str
    price: float
    as_of: datetime
    requested: datetime | date
    kind: Literal["intraday", "daily_close"]
    source: Literal["yfinance", "prices_db"]
    adjusted: bool
    is_stale: bool


def _default_quote_fn(ticker: str) -> tuple[float, datetime] | None:
    """yfinance 近端最新成交价(raw)。隔离导入,无网络/装包时不拖垮模块导入。"""
    try:
        import yfinance as yf

        # 注意:此 yfinance 版本 fast_info.get('last_price') 恒为 None(坏路径),
        # 必须用下标取值 fi['last_price'](属性/下标可用,实测 291.58 正常)
        fi = yf.Ticker(ticker).fast_info
        price = fi["last_price"]
        if price is None or price <= 0:
            return None
        return float(price), datetime.now(UTC)
    except Exception as exc:  # noqa: BLE001 — yfinance 异常面广,统一降级为"无价"由上层 fail-closed
        logger.warning("yfinance quote 失败 {}: {}", ticker, exc)
        return None


def _default_daily_fn(ticker: str, start: date, end: date) -> dict[date, float]:
    """yfinance 原始日收盘(auto_adjust=False 的 Close)。"""
    try:
        import yfinance as yf

        df = yf.download(ticker, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                         interval="1d", auto_adjust=False, progress=False)
        if df is None or df.empty:
            return {}
        close = df["Close"]
        if hasattr(close, "columns"):  # 单票时可能是单列 DataFrame(MultiIndex),取首列成 Series
            close = close.iloc[:, 0]
        out: dict[date, float] = {}
        for idx, val in close.items():
            px = float(val)  # type: ignore[arg-type]
            if px == px and px > 0:  # px==px 滤 NaN
                out[idx.date()] = px  # type: ignore[union-attr]
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance daily 失败 {} [{}~{}]: {}", ticker, start, end, exc)
        return {}


class PriceSource:
    """信号时点成交价 + 每日收盘价的权威读取。"""

    def __init__(
        self,
        prices_db: object = None,
        *,
        cache_db: object = None,
        quote_fn: QuoteFn = _default_quote_fn,
        daily_fn: DailyFn = _default_daily_fn,
    ) -> None:
        """prices_db: stock-picker 只读价库(默认 settings.prices_db_path);
        cache_db: 我方可写日收盘缓存(默认 data/prices/daily_close.duckdb,gitignore);
        quote_fn/daily_fn 可注入测试假实现。"""
        if prices_db is None or cache_db is None:
            from config.settings import get_settings

            settings = get_settings()
            if prices_db is None:
                prices_db = settings.prices_db_path
            if cache_db is None:
                cache_db = settings.data_dir / "prices" / "daily_close.duckdb"
        self._prices_db = Path(str(prices_db))
        self._cache_db = assert_writable_path(Path(str(cache_db)))  # 防参数转置:缓存绝不落 stock-picker 侧
        self._quote_fn = quote_fn
        self._daily_fn = daily_fn

    def price_at(self, ticker: str, ts: datetime, *, max_staleness_min: float = 120.0) -> PricePoint:
        """信号时点成交价(raw)。前向跟单 ts≈now → yfinance 近端 last。
        无价或超 max_staleness → PriceUnavailable(由 PaperBroker 映射 no_price/signal_stale)。"""
        if ts.tzinfo is None:
            raise ValueError(f"ts 必须带时区(UTC): {ts!r}")
        quote = self._quote_fn(ticker)
        if quote is None:
            raise PriceUnavailable(f"{ticker}: 无近端成交价(ticker 无效/停牌/yfinance 失败)")
        price, as_of = quote
        drift_min = abs((as_of - ts).total_seconds()) / 60.0
        if drift_min > max_staleness_min:
            raise PriceUnavailable(
                f"{ticker}: 最新价 as_of={as_of.isoformat()} 距请求 ts={ts.isoformat()} "
                f"偏 {drift_min:.0f}min > {max_staleness_min}min(拒返陈旧价)"
            )
        return PricePoint(ticker=ticker, price=price, as_of=as_of, requested=ts,
                          kind="intraday", source="yfinance", adjusted=False, is_stale=False)

    def close_on(self, ticker: str, d: date) -> PricePoint:
        """日收盘价。读序:我方缓存(raw)→ prices.db(adjusted)→ yfinance raw(+写回缓存);均无 → PriceUnavailable。

        新鲜度保证:prices.db 由 stock-picker 每日维护但非我控,任一日缺则 yfinance 按需补——
        前向跑因此不被 prices.db 滞后阻塞;yfinance 拉到的 raw 写回我方缓存(确定性复现 + 省限速 + 审计)。
        """
        ts = datetime(d.year, d.month, d.day, tzinfo=UTC)
        cached = self._cache_get(ticker, d)
        if cached is not None:
            return PricePoint(ticker=ticker, price=cached, as_of=ts, requested=d,
                              kind="daily_close", source="yfinance", adjusted=False, is_stale=False)
        db_close = self._prices_db_close(ticker, d)
        if db_close is not None:
            return PricePoint(ticker=ticker, price=db_close, as_of=ts, requested=d,
                              kind="daily_close", source="prices_db", adjusted=True, is_stale=False)
        yf_close = self._daily_fn(ticker, d, d).get(d)
        if yf_close is not None and yf_close > 0:
            self._cache_put([(ticker, d, float(yf_close))])
            return PricePoint(ticker=ticker, price=float(yf_close), as_of=ts, requested=d,
                              kind="daily_close", source="yfinance", adjusted=False, is_stale=False)
        raise PriceUnavailable(f"{ticker}: {d} 无日收盘价(我方缓存/prices.db/yfinance 均缺)")

    def warm_daily_close(self, tickers: Sequence[str], d: date) -> dict[str, int]:
        """前向 runner 每日预热:批量确保 tickers 在 d 有日收盘价(我方缓存 or prices.db),
        缺的经 yfinance 拉一次写回缓存。返回 {covered, fetched, missing}(missing=yfinance 也拿不到)。"""
        covered = fetched = 0
        missing: list[str] = []
        to_fetch: list[str] = []
        for t in dict.fromkeys(tickers):
            if self._cache_get(t, d) is not None or self._prices_db_close(t, d) is not None:
                covered += 1
            else:
                to_fetch.append(t)
        new_rows: list[tuple[str, date, float]] = []
        for t in to_fetch:
            px = self._daily_fn(t, d, d).get(d)
            if px is not None and px > 0:
                new_rows.append((t, d, float(px)))
                fetched += 1
            else:
                missing.append(t)
        if new_rows:
            self._cache_put(new_rows)
        report = {"covered": covered, "fetched": fetched, "missing": len(missing)}
        if missing:
            logger.warning("warm_daily_close {} 票 {} 无价(yfinance 也缺): {}", len(missing), d, missing)
        logger.info("warm_daily_close {}: {}", d, report)
        return report

    def coverage(self, tickers: Sequence[str], d: date) -> dict[str, list[str]]:
        """只读检查:tickers 在 d 的覆盖情况(不拉 yfinance)。返回 {covered, missing}(missing 需 warm/兜底)。"""
        covered: list[str] = []
        missing: list[str] = []
        for t in dict.fromkeys(tickers):
            if self._cache_get(t, d) is not None or self._prices_db_close(t, d) is not None:
                covered.append(t)
            else:
                missing.append(t)
        return {"covered": covered, "missing": missing}

    def _prices_db_close(self, ticker: str, d: date) -> float | None:
        """prices.db 只读取某票某日 close;无库/无行返回 None。"""
        if not self._prices_db.exists():
            return None
        with closing(connect_readonly(self._prices_db)) as conn:
            row = conn.execute(
                "SELECT close FROM price_cache WHERE ticker = ? AND date = ?",
                (ticker, d.isoformat()),
            ).fetchone()
        return float(row[0]) if row and row[0] is not None and row[0] > 0 else None

    def _cache_get(self, ticker: str, d: date) -> float | None:
        """我方日收盘缓存(raw)读取;无库/无行返回 None。"""
        if not self._cache_db.exists():
            return None
        con = duckdb.connect(str(self._cache_db), read_only=True)
        try:
            row = con.execute("SELECT close FROM daily_close WHERE ticker = ? AND date = ?",
                              [ticker, d]).fetchone()
        except duckdb.CatalogException:
            return None
        finally:
            con.close()
        return float(row[0]) if row and row[0] is not None and row[0] > 0 else None

    def _cache_put(self, rows: list[tuple[str, date, float]]) -> None:
        """写回我方缓存(幂等:同 (ticker,date) 已存在则跳过,首写定格);只落可写侧。"""
        if not rows:
            return
        assert_writable_path(self._cache_db)
        self._cache_db.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(self._cache_db))
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS daily_close ("
                "ticker TEXT NOT NULL, date DATE NOT NULL, close DOUBLE NOT NULL, "
                "source TEXT NOT NULL DEFAULT 'yfinance', fetched_ts TIMESTAMPTZ DEFAULT now(), "
                "PRIMARY KEY (ticker, date))"
            )
            con.executemany(
                "INSERT INTO daily_close (ticker, date, close) VALUES (?, ?, ?) ON CONFLICT DO NOTHING", rows)
        finally:
            con.close()
