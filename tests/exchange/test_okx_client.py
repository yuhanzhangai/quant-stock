"""OKX 原生客户端测试（mock 版本）。"""

from unittest.mock import patch

import pytest

from src.exchange.okx_client import OKXNativeClient
from tests.conftest import load_fixture


@pytest.fixture
def mock_okx_client() -> OKXNativeClient:
    """创建 mock 的 OKX 原生客户端。"""
    return OKXNativeClient()


class TestOKXNativeClient:
    @pytest.mark.asyncio
    async def test_fetch_funding_rate_history(self, mock_okx_client: OKXNativeClient) -> None:
        """测试资金费率历史获取。"""
        fixture = load_fixture("funding_rate_history.json")

        with patch.object(
            mock_okx_client._public_api,
            "funding_rate_history",
            return_value=fixture,
        ):
            result = await mock_okx_client.fetch_funding_rate_history("BTC-USDT-SWAP")

            assert len(result) == len(fixture["data"])
            assert "fundingRate" in result[0]
            assert "fundingTime" in result[0]

    @pytest.mark.asyncio
    async def test_fetch_open_interest(self, mock_okx_client: OKXNativeClient) -> None:
        """测试持仓量获取。"""
        fixture = load_fixture("open_interest.json")

        with patch.object(
            mock_okx_client._public_api,
            "get_open_interest",
            return_value=fixture,
        ):
            result = await mock_okx_client.fetch_open_interest(
                inst_type="SWAP", inst_id="BTC-USDT-SWAP"
            )

            assert len(result) > 0
            assert "oi" in result[0]

    @pytest.mark.asyncio
    async def test_check_response_error(self, mock_okx_client: OKXNativeClient) -> None:
        """测试 API 返回错误时抛异常。"""
        error_response = {"code": "50013", "msg": "Invalid sign", "data": []}

        with pytest.raises(RuntimeError, match="OKX API error"):
            mock_okx_client._check_response(error_response, "test")

    @pytest.mark.asyncio
    async def test_fetch_current_funding_rate(self, mock_okx_client: OKXNativeClient) -> None:
        """测试当前资金费率获取。"""
        mock_response = {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "fundingRate": "0.0001",
                    "nextFundingRate": "0.00015",
                    "fundingTime": "1776909600000",
                }
            ],
        }

        with patch.object(
            mock_okx_client._public_api,
            "get_funding_rate",
            return_value=mock_response,
        ):
            result = await mock_okx_client.fetch_current_funding_rate("BTC-USDT-SWAP")

            assert result["instId"] == "BTC-USDT-SWAP"
            assert result["fundingRate"] == "0.0001"
