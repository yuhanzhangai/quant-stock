"""下单意图/结果模型校验测试。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.execution.firstrade_agent.models import (
    AccountSnapshot,
    OrderIntent,
    OrderType,
    Side,
)


class TestOrderIntent:
    def test_symbol_normalized_upper(self):
        intent = OrderIntent(symbol=" nvda ", side=Side.BUY, qty=10, limit_price=Decimal("100"))
        assert intent.symbol == "NVDA"

    @pytest.mark.parametrize("bad", ["BTC-USDT", "", "TOOLONG", "AB1", "NVDA.US"])
    def test_non_us_ticker_rejected(self, bad):
        with pytest.raises(ValidationError):
            OrderIntent(symbol=bad, side=Side.BUY, qty=1, limit_price=Decimal("1"))

    @pytest.mark.parametrize("qty", [0, -5])
    def test_non_positive_qty_rejected(self, qty):
        with pytest.raises(ValidationError):
            OrderIntent(symbol="AAPL", side=Side.SELL, qty=qty, limit_price=Decimal("1"))

    def test_limit_order_requires_price(self):
        with pytest.raises(ValidationError, match="limit_price"):
            OrderIntent(symbol="AAPL", side=Side.BUY, qty=1, order_type=OrderType.LIMIT)

    def test_market_order_rejects_price(self):
        with pytest.raises(ValidationError):
            OrderIntent(
                symbol="AAPL",
                side=Side.BUY,
                qty=1,
                order_type=OrderType.MARKET,
                limit_price=Decimal("100"),
            )

    def test_market_order_ok_without_price(self):
        intent = OrderIntent(symbol="AAPL", side=Side.SELL, qty=3, order_type=OrderType.MARKET)
        assert intent.limit_price is None


class TestAccountSnapshot:
    def test_defaults(self):
        snap = AccountSnapshot()
        assert snap.source == "firstrade_paper"
        assert snap.positions == []
        assert snap.ts.tzinfo is not None  # 时间必须带时区
