"""特斯拉新闻自动获取模块。

数据源：
1. Google News RSS（免费，无需 API Key）
2. 手动事件目录补充

功能：
- 自动抓取最新 TSLA 相关新闻
- 关键词情绪分析（基于标题）
- 与手动事件目录合并
- 缓存到本地 JSON
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import feedparser
from loguru import logger


@dataclass
class NewsItem:
    """单条新闻。"""

    title: str
    published: str  # ISO 格式
    source: str
    url: str
    sentiment: str = "unknown"  # bullish / bearish / neutral / unknown
    sentiment_score: float = 0.0
    keywords_matched: list[str] = field(default_factory=list)


# =========================================================================
# 关键词情绪词典
# =========================================================================
BULLISH_KEYWORDS = [
    "beat",
    "beats",
    "exceed",
    "surpass",
    "surge",
    "soar",
    "rally",
    "jump",
    "record",
    "upgrade",
    "buy",
    "outperform",
    "strong",
    "growth",
    "profit",
    "delivery",
    "deliveries up",
    "fsd",
    "autonomous",
    "robotaxi launch",
    "tariff pause",
    "tariff relief",
    "tariff exempt",
    "approval",
    "approved",
    "breakthrough",
    "partnership",
    "上涨",
    "突破",
    "利好",
    "超预期",
    "创新高",
    "反弹",
    "回升",
    "交付增长",
    "自动驾驶突破",
    "关税暂停",
    "关税豁免",
]

BEARISH_KEYWORDS = [
    "miss",
    "misses",
    "decline",
    "drop",
    "fall",
    "crash",
    "plunge",
    "tumble",
    "downgrade",
    "sell",
    "underperform",
    "weak",
    "loss",
    "recall",
    "tariff",
    "tariff hike",
    "trade war",
    "investigation",
    "probe",
    "lawsuit",
    "layoff",
    "cut",
    "slash",
    "boycott",
    "protest",
    "ban",
    "delivery miss",
    "disappointing",
    "below expectations",
    "下跌",
    "暴跌",
    "利空",
    "不及预期",
    "召回",
    "裁员",
    "抵制",
    "关税",
    "调查",
    "诉讼",
    "下调",
]

NEUTRAL_KEYWORDS = [
    "maintain",
    "hold",
    "unchanged",
    "steady",
    "flat",
    "mixed",
    "维持",
    "不变",
    "持平",
]


def _analyze_sentiment(title: str) -> tuple[str, float, list[str]]:
    """基于标题关键词分析情绪。

    Args:
        title: 新闻标题

    Returns:
        (sentiment, score, matched_keywords)
    """
    title_lower = title.lower()
    bullish_matches = [kw for kw in BULLISH_KEYWORDS if kw.lower() in title_lower]
    bearish_matches = [kw for kw in BEARISH_KEYWORDS if kw.lower() in title_lower]
    neutral_matches = [kw for kw in NEUTRAL_KEYWORDS if kw.lower() in title_lower]

    bull_score = len(bullish_matches)
    bear_score = len(bearish_matches)

    all_matched = bullish_matches + bearish_matches + neutral_matches

    if bull_score > bear_score:
        score = min(bull_score / 3.0, 1.0)
        return "bullish", score, all_matched
    elif bear_score > bull_score:
        score = -min(bear_score / 3.0, 1.0)
        return "bearish", score, all_matched
    elif neutral_matches:
        return "neutral", 0.0, all_matched
    else:
        return "unknown", 0.0, []


def fetch_google_news_rss(
    query: str = "Tesla TSLA",
    max_items: int = 50,
) -> list[NewsItem]:
    """从 Google News RSS 获取新闻。

    Args:
        query: 搜索关键词
        max_items: 最大返回条数

    Returns:
        NewsItem 列表
    """
    # Google News RSS URL
    encoded_query = query.replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"

    logger.info(f"正在从 Google News RSS 获取新闻 | query: {query}")

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error(f"RSS 获取失败: {e}")
        return []

    if not feed.entries:
        logger.warning("未获取到任何新闻条目")
        return []

    items: list[NewsItem] = []
    for entry in feed.entries[:max_items]:
        title = entry.get("title", "")
        # 解析发布时间
        published = ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6], tzinfo=UTC)
            published = dt.isoformat()
        elif hasattr(entry, "published"):
            published = entry.published

        source = entry.get("source", {}).get("title", "Unknown")
        link = entry.get("link", "")

        # 情绪分析
        sentiment, score, matched = _analyze_sentiment(title)

        items.append(
            NewsItem(
                title=title,
                published=published,
                source=source,
                url=link,
                sentiment=sentiment,
                sentiment_score=score,
                keywords_matched=matched,
            )
        )

    # 统计
    sentiments = {"bullish": 0, "bearish": 0, "neutral": 0, "unknown": 0}
    for item in items:
        sentiments[item.sentiment] = sentiments.get(item.sentiment, 0) + 1

    logger.info(
        f"获取 {len(items)} 条新闻 | "
        f"利好: {sentiments['bullish']} | 利空: {sentiments['bearish']} | "
        f"中性: {sentiments['neutral']} | 未知: {sentiments['unknown']}"
    )
    return items


def fetch_tsla_news_cn(max_items: int = 30) -> list[NewsItem]:
    """从中文 RSS 源获取特斯拉新闻。

    Args:
        max_items: 最大返回条数

    Returns:
        NewsItem 列表
    """
    url = "https://news.google.com/rss/search?q=特斯拉+Tesla&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"

    logger.info("正在从 Google News RSS (中文) 获取新闻")
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error(f"中文 RSS 获取失败: {e}")
        return []

    items: list[NewsItem] = []
    for entry in feed.entries[:max_items]:
        title = entry.get("title", "")
        published = ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6], tzinfo=UTC)
            published = dt.isoformat()

        source = entry.get("source", {}).get("title", "Unknown")
        link = entry.get("link", "")

        sentiment, score, matched = _analyze_sentiment(title)
        items.append(
            NewsItem(
                title=title,
                published=published,
                source=source,
                url=link,
                sentiment=sentiment,
                sentiment_score=score,
                keywords_matched=matched,
            )
        )

    logger.info(f"获取 {len(items)} 条中文新闻")
    return items


def save_news_cache(items: list[NewsItem], path: str | None = None) -> Path:
    """缓存新闻到本地 JSON。

    Args:
        items: 新闻列表
        path: 保存路径

    Returns:
        保存路径
    """
    if path is None:
        out_dir = Path("data/raw/news")
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"tsla_news_{timestamp}.json"
    else:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    data = [asdict(item) for item in items]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"新闻已缓存 → {out_path} ({len(items)} 条)")
    return out_path


def load_news_cache(path: str | Path) -> list[NewsItem]:
    """从缓存加载新闻。

    Args:
        path: JSON 文件路径

    Returns:
        NewsItem 列表
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [NewsItem(**item) for item in data]


def get_sentiment_summary(items: list[NewsItem]) -> dict:
    """汇总新闻情绪。

    Args:
        items: 新闻列表

    Returns:
        情绪汇总字典
    """
    if not items:
        return {"overall": "unknown", "score": 0.0, "count": 0}

    scores = [item.sentiment_score for item in items]
    avg_score = sum(scores) / len(scores)
    bullish_count = sum(1 for i in items if i.sentiment == "bullish")
    bearish_count = sum(1 for i in items if i.sentiment == "bearish")

    if avg_score > 0.1:
        overall = "bullish"
    elif avg_score < -0.1:
        overall = "bearish"
    else:
        overall = "neutral"

    return {
        "overall": overall,
        "avg_score": round(avg_score, 4),
        "count": len(items),
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "bullish_ratio": round(bullish_count / len(items), 2) if items else 0,
        "bearish_ratio": round(bearish_count / len(items), 2) if items else 0,
    }


if __name__ == "__main__":
    # 快速测试
    print("=== 英文新闻 ===")
    en_news = fetch_google_news_rss("Tesla TSLA stock", max_items=20)
    for item in en_news[:5]:
        print(f"  [{item.sentiment:>8}] {item.title[:80]}")
        if item.keywords_matched:
            print(f"           匹配: {item.keywords_matched}")

    print("\n=== 情绪汇总 ===")
    summary = get_sentiment_summary(en_news)
    for k, v in summary.items():
        print(f"  {k}: {v}")

    # 缓存
    all_news = en_news
    cn_news = fetch_tsla_news_cn(max_items=10)
    all_news.extend(cn_news)
    save_news_cache(all_news)
