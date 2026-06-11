"""warm_prices CLI 测试:注入 PriceSource/票源,验退出码语义(全离线)。"""

from datetime import date

import pytest

from src.data import warm_prices


def test_exit0_all_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Src:
        def warm_daily_close(self, tickers, d):
            return {"covered": 2, "fetched": 0, "missing": 0}

    monkeypatch.setattr(warm_prices, "PriceSource", lambda: _Src())
    assert warm_prices.main(["--date", "2026-06-10", "--tickers", "NVDA,AMD"]) == 0


def test_exit2_some_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Src:
        def warm_daily_close(self, tickers, d):
            return {"covered": 1, "fetched": 0, "missing": 1}

    monkeypatch.setattr(warm_prices, "PriceSource", lambda: _Src())
    assert warm_prices.main(["--date", "2026-06-10", "--tickers", "NVDA,SPCX"]) == 2


def test_exit3_all_missing_yfinance_down(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Src:
        def warm_daily_close(self, tickers, d):
            return {"covered": 0, "fetched": 0, "missing": 2}

    monkeypatch.setattr(warm_prices, "PriceSource", lambda: _Src())
    assert warm_prices.main(["--date", "2026-06-10", "--tickers", "NVDA,AMD"]) == 3


def test_exit3_no_tickers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(warm_prices, "_candidate_tickers", lambda: [])
    assert warm_prices.main(["--date", "2026-06-10"]) == 3


def test_tickers_from_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    class _Src:
        def warm_daily_close(self, tickers, d):
            seen["tickers"] = tickers
            seen["date"] = d
            return {"covered": len(tickers), "fetched": 0, "missing": 0}

    monkeypatch.setattr(warm_prices, "PriceSource", lambda: _Src())
    monkeypatch.setattr(warm_prices, "_candidate_tickers", lambda: ["AMD", "NVDA"])
    assert warm_prices.main(["--date", "2026-06-09"]) == 0
    assert seen["tickers"] == ["AMD", "NVDA"]
    assert seen["date"] == date(2026, 6, 9)
