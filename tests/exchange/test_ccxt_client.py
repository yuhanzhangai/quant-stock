"""CCXT 客户端测试（mock 版本）。"""

from unittest.mock import AsyncMock, patch

import pytest

from src.exchange.ccxt_client import CCXTClient
from tests.conftest import load_fixture


@pytest.fixture
def mock_ccxt_client() -> CCXTClient:
    """创建不连接真实交易所的 mock 客户端。"""
    client = CCXTClient()
    return client


class TestCCXTClient:
    @pytest.mark.asyncio
    async def test_fetch_ohlcv(self, mock_ccxt_client: CCXTClient) -> None:
        """测试 K 线获取（mock）。"""
        fixture = load_fixture("market_candles.json")
        # CCXT 返回格式: [[ts, o, h, l, c, v], ...]
        mock_data = [
            [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
            for c in fixture["data"]
        ]

        with patch.object(
            mock_ccxt_client._exchange, "fetch_ohlcv", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_data
            result = await mock_ccxt_client.fetch_ohlcv("BTC/USDT", "1h", limit=5)

            assert len(result) == len(mock_data)
            assert result[0][0] == mock_data[0][0]  # timestamp
            mock_fetch.assert_called_once()

        await mock_ccxt_client.close()

    @pytest.mark.asyncio
    async def test_fetch_tickers(self, mock_ccxt_client: CCXTClient) -> None:
        """测试 Ticker 获取（mock）。"""
        mock_data = {
            "BTC/USDT": {"symbol": "BTC/USDT", "last": 93000.0},
            "ETH/USDT": {"symbol": "ETH/USDT", "last": 1750.0},
        }

        with patch.object(
            mock_ccxt_client._exchange, "fetch_tickers", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_data
            result = await mock_ccxt_client.fetch_tickers(["BTC/USDT", "ETH/USDT"])

            assert "BTC/USDT" in result
            assert result["BTC/USDT"]["last"] == 93000.0
            mock_fetch.assert_called_once()

        await mock_ccxt_client.close()

    @pytest.mark.asyncio
    async def test_fetch_markets(self, mock_ccxt_client: CCXTClient) -> None:
        """测试市场信息获取（mock）。"""
        mock_markets = {
            "BTC/USDT": {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT"},
        }

        with patch.object(
            mock_ccxt_client._exchange, "load_markets", new_callable=AsyncMock
        ) as mock_load:
            mock_load.return_value = mock_markets
            result = await mock_ccxt_client.fetch_markets()

            assert len(result) == 1
            assert result[0]["symbol"] == "BTC/USDT"

        await mock_ccxt_client.close()

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_range(self, mock_ccxt_client: CCXTClient) -> None:
        """测试分页拉取 K 线。"""
        # 第一页返回满页，第二页返回不满（触发停止）
        page1 = [[1000 + i, 1.0, 2.0, 0.5, 1.5, 100.0] for i in range(100)]
        page2 = [[1100 + i, 1.0, 2.0, 0.5, 1.5, 100.0] for i in range(50)]

        call_count = 0

        async def mock_fetch_ohlcv(symbol, timeframe="1h", since=None, limit=100):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return page1
            return page2

        with patch.object(
            mock_ccxt_client._exchange, "fetch_ohlcv", side_effect=mock_fetch_ohlcv
        ):
            result = await mock_ccxt_client.fetch_ohlcv_range("BTC/USDT", "1h")

            assert len(result) == 150
            assert call_count == 2

        await mock_ccxt_client.close()
