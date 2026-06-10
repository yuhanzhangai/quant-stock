"""演示脚本：测试 OKX 客户端拉取真实数据。"""

import asyncio
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.exchange.okx_client import OKXNativeClient


async def demo_ccxt() -> None:
    """演示 CCXT 客户端功能。"""
    settings = get_settings()

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
        use_simulated=settings.okx_use_simulated,
    ) as client:
        # 1. 拉 BTC-USDT 最近 10 根 1h K线
        logger.info("--- 拉取 BTC/USDT 1h K线 ---")
        candles = await client.fetch_ohlcv("BTC/USDT", "1h", limit=10)
        for c in candles[-3:]:
            logger.info(f"  时间: {c[0]} | 开: {c[1]} | 高: {c[2]} | 低: {c[3]} | 收: {c[4]} | 量: {c[5]}")
        logger.info(f"  共 {len(candles)} 根K线")


async def demo_okx_native() -> None:
    """演示 OKX 原生客户端功能。"""
    settings = get_settings()

    client = OKXNativeClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
        use_simulated=settings.okx_use_simulated,
    )

    # 1. 资金费率
    logger.info("--- 拉取 BTC-USDT-SWAP 资金费率 ---")
    funding = await client.fetch_funding_rate_history("BTC-USDT-SWAP", limit="5")
    for f in funding[:3]:
        logger.info(f"  时间: {f['fundingTime']} | 费率: {f['fundingRate']} | 实际费率: {f.get('realizedRate', 'N/A')}")

    # 2. 持仓量
    logger.info("--- 拉取 BTC-USDT-SWAP 持仓量 ---")
    oi = await client.fetch_open_interest(inst_type="SWAP", inst_id="BTC-USDT-SWAP")
    if oi:
        logger.info(f"  合约: {oi[0]['instId']} | 持仓量: {oi[0]['oi']} | 持仓额: {oi[0].get('oiCcy', 'N/A')}")

    # 3. 当前资金费率
    logger.info("--- 当前资金费率 ---")
    current = await client.fetch_current_funding_rate("BTC-USDT-SWAP")
    if current:
        logger.info(
            f"  当前费率: {current.get('fundingRate', 'N/A')} | 下次费率: {current.get('nextFundingRate', 'N/A')}"
        )


async def main() -> None:
    logger.info("=" * 60)
    logger.info("OKX 客户端演示")
    logger.info("=" * 60)

    await demo_ccxt()
    logger.info("")
    await demo_okx_native()

    logger.info("")
    logger.success("演示完成！")


if __name__ == "__main__":
    asyncio.run(main())
