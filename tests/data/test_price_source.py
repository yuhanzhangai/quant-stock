"""price_source 测试:注入假 yfinance + tmp prices.db,全离线(不打网络)。"""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from src.data.price_source import PricePoint, PriceSource, PriceUnavailable

_PRICES_SCHEMA = """
CREATE TABLE price_cache (ticker TEXT, date TEXT, close REAL, fetched_at INTEGER);
CREATE TABLE price_meta (ticker TEXT, last_pull INTEGER, min_date TEXT, max_date TEXT);
"""


@pytest.fixture
def prices_db(tmp_path: Path) -> Path:
    path = tmp_path / "prices.db"
    c = sqlite3.connect(path)
    c.executescript(_PRICES_SCHEMA)
    c.execute("INSERT INTO price_cache VALUES ('NVDA', '2026-06-09', 120.5, 1)")
    c.execute("INSERT INTO price_cache VALUES ('NVDA', '2026-06-10', 121.0, 1)")
    c.commit()
    c.close()
    return path


def _src(prices_db: Path, *, cache_db: Path | None = None, quote=None, daily=None) -> PriceSource:
    return PriceSource(
        prices_db,
        cache_db=cache_db if cache_db is not None else prices_db.parent / "cache" / "daily_close.duckdb",
        quote_fn=quote if quote is not None else (lambda t: None),
        daily_fn=daily if daily is not None else (lambda t, s, e: {}),
    )


# ── price_at(信号成交价)──


def test_price_at_returns_raw_intraday(prices_db: Path) -> None:
    now = datetime.now(UTC)
    src = _src(prices_db, quote=lambda t: (122.3, now))
    pp = src.price_at("NVDA", now)
    assert isinstance(pp, PricePoint)
    assert pp.price == 122.3
    assert pp.source == "yfinance" and pp.kind == "intraday"
    assert pp.adjusted is False  # 成交价要原始价不要复权价
    assert pp.is_stale is False


def test_price_at_no_quote_fails_closed(prices_db: Path) -> None:
    """拿不到价绝不造假价 —— 抛 PriceUnavailable 让 PaperBroker skip(no_price)。"""
    src = _src(prices_db, quote=lambda t: None)
    with pytest.raises(PriceUnavailable, match="无近端成交价"):
        src.price_at("BADX", datetime.now(UTC))


def test_price_at_rejects_stale_quote(prices_db: Path) -> None:
    ts = datetime.now(UTC)
    old = ts - timedelta(hours=5)  # 最新价比请求老 5h,超默认 120min
    src = _src(prices_db, quote=lambda t: (100.0, old))
    with pytest.raises(PriceUnavailable, match="拒返陈旧价"):
        src.price_at("NVDA", ts, max_staleness_min=120.0)


def test_price_at_within_staleness_ok(prices_db: Path) -> None:
    ts = datetime.now(UTC)
    recent = ts - timedelta(minutes=30)
    src = _src(prices_db, quote=lambda t: (100.0, recent))
    assert src.price_at("NVDA", ts).price == 100.0


def test_price_at_requires_tz(prices_db: Path) -> None:
    src = _src(prices_db, quote=lambda t: (1.0, datetime.now(UTC)))
    with pytest.raises(ValueError, match="必须带时区"):
        src.price_at("NVDA", datetime(2026, 6, 10, 12, 0))  # noqa: DTZ001 — 故意 naive 触发守卫


# ── close_on(每日 mark)──


def test_close_on_prefers_prices_db_adjusted(prices_db: Path) -> None:
    src = _src(prices_db, daily=lambda t, s, e: {date(2026, 6, 10): 999.0})  # 不该被用到
    pp = src.close_on("NVDA", date(2026, 6, 10))
    assert pp.price == 121.0  # 取自 prices.db,非 yfinance 999
    assert pp.source == "prices_db" and pp.adjusted is True
    assert pp.kind == "daily_close"


def test_close_on_falls_back_to_yfinance_raw(prices_db: Path) -> None:
    src = _src(prices_db, daily=lambda t, s, e: {date(2026, 6, 11): 123.4})
    pp = src.close_on("NVDA", date(2026, 6, 11))  # prices.db 无此日
    assert pp.price == 123.4
    assert pp.source == "yfinance" and pp.adjusted is False


def test_close_on_both_missing_fails_closed(prices_db: Path) -> None:
    src = _src(prices_db, daily=lambda t, s, e: {})
    with pytest.raises(PriceUnavailable, match="无日收盘价"):
        src.close_on("NVDA", date(2020, 1, 1))


def test_close_on_ignores_nonpositive_db_close(tmp_path: Path) -> None:
    path = tmp_path / "prices.db"
    c = sqlite3.connect(path)
    c.executescript(_PRICES_SCHEMA)
    c.execute("INSERT INTO price_cache VALUES ('X', '2026-06-10', 0.0, 1)")  # 脏 0 价
    c.commit()
    c.close()
    src = _src(path, daily=lambda t, s, e: {date(2026, 6, 10): 50.0})
    pp = src.close_on("X", date(2026, 6, 10))
    assert pp.price == 50.0 and pp.source == "yfinance"  # 0 价被跳过转 yfinance


def test_close_on_missing_db_file_uses_yfinance(tmp_path: Path) -> None:
    src = _src(tmp_path / "nonexistent.db", daily=lambda t, s, e: {date(2026, 6, 10): 77.0})
    assert src.close_on("NVDA", date(2026, 6, 10)).price == 77.0


def test_yfinance_fallback_writes_through_cache(prices_db: Path, tmp_path: Path) -> None:
    """yfinance 兜底拉到的 raw 价写回我方缓存:第二次同请求命中缓存,不再调 yfinance。"""
    calls = {"n": 0}

    def daily(t, s, e):
        calls["n"] += 1
        return {date(2026, 6, 11): 123.4}

    src = _src(prices_db, cache_db=tmp_path / "c.duckdb", daily=daily)
    pp1 = src.close_on("NVDA", date(2026, 6, 11))  # prices.db 无 → yfinance → 写缓存
    assert pp1.source == "yfinance" and pp1.price == 123.4 and calls["n"] == 1
    pp2 = src.close_on("NVDA", date(2026, 6, 11))  # 命中缓存,不再调 yfinance
    assert pp2.price == 123.4 and calls["n"] == 1


def test_cache_takes_precedence_over_prices_db(prices_db: Path, tmp_path: Path) -> None:
    """读序:我方缓存(raw)优先于 prices.db(adjusted)。06-10 在 prices.db=121.0,缓存写 999.0,读应取缓存。"""
    src = _src(prices_db, cache_db=tmp_path / "c.duckdb")
    src._cache_put([("NVDA", date(2026, 6, 10), 999.0)])
    pp = src.close_on("NVDA", date(2026, 6, 10))
    assert pp.price == 999.0 and pp.source == "yfinance" and pp.adjusted is False


def test_warm_daily_close_reports(prices_db: Path, tmp_path: Path) -> None:
    """预热:prices.db 已有的算 covered;缺的经 yfinance 拉 fetched;yfinance 也无的 missing。"""

    def daily(t, s, e):
        return {date(2026, 6, 11): 50.0} if t == "AMD" else {}

    src = _src(prices_db, cache_db=tmp_path / "c.duckdb", daily=daily)
    rep = src.warm_daily_close(["NVDA", "AMD", "BADX"], date(2026, 6, 11))
    # NVDA 06-11 prices.db 无(fixture 只有 06-09/06-10)→ yfinance 无 NVDA → missing
    # AMD → yfinance 有 → fetched;BADX → 无 → missing
    assert rep == {"covered": 0, "fetched": 1, "missing": 2}
    rep2 = src.warm_daily_close(["NVDA"], date(2026, 6, 10))  # prices.db 有 06-10
    assert rep2 == {"covered": 1, "fetched": 0, "missing": 0}


def test_coverage_readonly_no_fetch(prices_db: Path, tmp_path: Path) -> None:
    """coverage 只读不拉 yfinance:prices.db 有的 covered,无的 missing。"""
    calls = {"n": 0}

    def daily(t, s, e):
        calls["n"] += 1
        return {}

    src = _src(prices_db, cache_db=tmp_path / "c.duckdb", daily=daily)
    cov = src.coverage(["NVDA", "AMD"], date(2026, 6, 10))
    assert cov["covered"] == ["NVDA"] and cov["missing"] == ["AMD"]
    assert calls["n"] == 0  # 绝不在 coverage 里打网络


def test_cache_never_on_stock_picker_side(prices_db: Path) -> None:
    """缓存路径守卫:cache_db 落 stock-picker 只读侧直接 ValueError。"""
    from src.signals.paths import STOCK_PICKER_HOME

    with pytest.raises(ValueError, match="只读侧"):
        PriceSource(prices_db, cache_db=STOCK_PICKER_HOME / "evil_cache.duckdb")


def test_never_writes_prices_db(prices_db: Path) -> None:
    """只读铁律:走一轮读后 prices.db 内容/连接只读不变。"""
    src = _src(prices_db, quote=lambda t: (1.0, datetime.now(UTC)), daily=lambda t, s, e: {})
    src.close_on("NVDA", date(2026, 6, 10))
    # 只读连接打开下写应失败
    ro = sqlite3.connect(f"file:{prices_db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO price_cache VALUES ('Z','2026-06-10',1,1)")
    ro.close()
