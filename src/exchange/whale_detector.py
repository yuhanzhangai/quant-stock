"""鲸鱼检测模块：基于 OKX trades API 的大单监控。

两个模式：
1. 历史模式：拉取历史 trades 分析大单分布
2. 实时模式：WebSocket 订阅 trades 流，实时检测大单

大单定义：单笔成交额 > 阈值（如 $50,000）
鲸鱼信号：时间窗口内大单净方向（买入 vs 卖出）
"""

import time
from typing import Any

import aiohttp
from loguru import logger

from src.exchange.rate_limiter import RateLimiterManager


class WhaleDetector:
    """鲸鱼检测器。"""

    def __init__(
        self,
        threshold_usd: float = 50_000,
        window_seconds: int = 60,
    ) -> None:
        self._threshold = threshold_usd
        self._window = window_seconds
        self._rate_limiter = RateLimiterManager()

    async def fetch_recent_trades(
        self, inst_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """拉取最近成交记录。

        Args:
            inst_id: 如 "BTC-USDT"
            limit: 最多 500

        Returns:
            成交记录列表
        """
        await self._rate_limiter.acquire("default")
        url = f"https://www.okx.com/api/v5/market/trades?instId={inst_id}&limit={limit}"

        start = time.monotonic()
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                elapsed = (time.monotonic() - start) * 1000

                if data.get("code") != "0":
                    logger.error(f"trades API 错误: {data.get('msg')}")
                    return []

                trades = data.get("data", [])
                logger.debug(f"trades | {inst_id} | {len(trades)} 笔 | {elapsed:.0f}ms")
                return trades

    def detect_whales(
        self, trades: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """从成交记录中检测鲸鱼活动。

        Returns:
            {
                'whale_count': 大单数量,
                'whale_buy_volume': 大单买入总额 (USD),
                'whale_sell_volume': 大单卖出总额 (USD),
                'net_direction': 'BUY' / 'SELL' / 'NEUTRAL',
                'whale_trades': 大单列表,
            }
        """
        whale_trades = []
        buy_vol = 0.0
        sell_vol = 0.0

        for t in trades:
            price = float(t.get("px", 0))
            size = float(t.get("sz", 0))
            side = t.get("side", "")
            trade_usd = price * size

            if trade_usd >= self._threshold:
                whale_trades.append({
                    "price": price,
                    "size": size,
                    "usd": trade_usd,
                    "side": side,
                    "ts": t.get("ts", ""),
                })
                if side == "buy":
                    buy_vol += trade_usd
                else:
                    sell_vol += trade_usd

        net = buy_vol - sell_vol
        direction = "BUY" if net > self._threshold else "SELL" if net < -self._threshold else "NEUTRAL"

        return {
            "whale_count": len(whale_trades),
            "whale_buy_volume": round(buy_vol, 2),
            "whale_sell_volume": round(sell_vol, 2),
            "net_direction": direction,
            "net_usd": round(net, 2),
            "whale_trades": whale_trades,
        }

    async def scan(self, inst_id: str) -> dict[str, Any]:
        """扫描某个币种的鲸鱼活动。"""
        trades = await self.fetch_recent_trades(inst_id, limit=500)
        result = self.detect_whales(trades)
        if result["whale_count"] > 0:
            logger.info(
                f"🐳 {inst_id} | 大单: {result['whale_count']} 笔 | "
                f"买入: ${result['whale_buy_volume']:,.0f} | "
                f"卖出: ${result['whale_sell_volume']:,.0f} | "
                f"方向: {result['net_direction']}"
            )
        return result
