"""新闻情绪辅助模块：从本地 cryptocurrency.cv 获取市场情绪。

仅作为辅助判断，不影响核心策略信号。
当 API 不可用时静默降级（返回 neutral）。
"""

import aiohttp
from loguru import logger

NEWS_API_BASE = "http://localhost:3001/api"


async def get_market_sentiment() -> dict:
    """获取市场情绪概览。

    Returns:
        {
            'news_count': 最近新闻数量,
            'sentiment': 'bullish' / 'bearish' / 'neutral',
            'top_headlines': 最新标题列表,
            'available': API 是否可用,
        }
    """
    try:
        async with aiohttp.ClientSession() as session:
            # 拉最新新闻
            async with session.get(
                f"{NEWS_API_BASE}/news?limit=5", timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return _default_sentiment()
                data = await resp.json()

            articles = data.get("articles", [])
            if not articles:
                return _default_sentiment()

            headlines = [a.get("title", "") for a in articles[:5]]

            # 简单情绪判断（基于关键词）
            bullish_words = ["surge", "rally", "breakout", "soar", "pump", "bull", "high", "gain", "up"]
            bearish_words = ["crash", "dump", "plunge", "drop", "fall", "bear", "low", "loss", "down", "fear"]

            bull_count = sum(
                1 for h in headlines for w in bullish_words if w.lower() in h.lower()
            )
            bear_count = sum(
                1 for h in headlines for w in bearish_words if w.lower() in h.lower()
            )

            if bull_count > bear_count + 2:
                sentiment = "bullish"
            elif bear_count > bull_count + 2:
                sentiment = "bearish"
            else:
                sentiment = "neutral"

            logger.debug(
                f"NewsAPI | {len(articles)} articles | "
                f"bull:{bull_count} bear:{bear_count} -> {sentiment}"
            )

            return {
                "news_count": len(articles),
                "sentiment": sentiment,
                "top_headlines": headlines,
                "available": True,
            }

    except Exception as e:
        logger.debug(f"NewsAPI 不可用: {e}")
        return _default_sentiment()


def _default_sentiment() -> dict:
    return {
        "news_count": 0,
        "sentiment": "neutral",
        "top_headlines": [],
        "available": False,
    }
