"""市场大事件数据库 + 事件影响分析。

记录 crypto 历史重大事件，分析策略在各类事件前后的表现。
事件类型：监管、宏观经济、技术升级、黑天鹅、ETF/机构。
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
from loguru import logger


# 预置的历史大事件（UTC 时间戳）
PRESET_EVENTS = [
    # 2024 年
    {"date": "2024-01-10", "type": "ETF", "impact": "bullish",
     "title": "BTC Spot ETF 获批", "desc": "SEC 批准 11 只比特币现货 ETF"},
    {"date": "2024-03-14", "type": "PRICE", "impact": "bullish",
     "title": "BTC 创历史新高 73K", "desc": "ETF 资金推动 BTC 突破 73000"},
    {"date": "2024-04-20", "type": "PROTOCOL", "impact": "neutral",
     "title": "BTC 第四次减半", "desc": "区块奖励从 6.25 降至 3.125 BTC"},
    {"date": "2024-05-23", "type": "ETF", "impact": "bullish",
     "title": "ETH Spot ETF 获批预期", "desc": "SEC 180 态度转变，ETH ETF 申请获推进"},
    {"date": "2024-07-23", "type": "ETF", "impact": "bullish",
     "title": "ETH Spot ETF 上市", "desc": "以太坊现货 ETF 正式在美国上市"},
    {"date": "2024-08-05", "type": "MACRO", "impact": "bearish",
     "title": "日元套利平仓暴跌", "desc": "日本央行加息引发全球风险资产暴跌"},
    {"date": "2024-09-18", "type": "MACRO", "impact": "bullish",
     "title": "美联储首次降息 50bp", "desc": "联邦基金利率降至 4.75-5.0%"},
    {"date": "2024-11-05", "type": "REGULATION", "impact": "bullish",
     "title": "Trump 当选", "desc": "加密友好总统当选，市场大涨"},
    {"date": "2024-12-04", "type": "PRICE", "impact": "bullish",
     "title": "BTC 突破 100K", "desc": "比特币首次突破 10 万美元"},

    # 2025 年
    {"date": "2025-01-20", "type": "REGULATION", "impact": "bullish",
     "title": "Trump 就职", "desc": "加密友好政策预期，签署行政令"},
    {"date": "2025-03-02", "type": "REGULATION", "impact": "bullish",
     "title": "美国战略 BTC 储备", "desc": "Trump 宣布建立国家比特币战略储备"},
    {"date": "2025-04-07", "type": "MACRO", "impact": "bearish",
     "title": "关税升级恐慌", "desc": "全球贸易战升级导致风险资产大跌"},

    # 2026 年
    {"date": "2026-01-15", "type": "MACRO", "impact": "neutral",
     "title": "美联储暂停降息", "desc": "通胀回升导致美联储暂停降息周期"},
]


class MarketEventDB:
    """市场大事件数据库。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._create_tables()
        self._load_presets()
        logger.info(f"MarketEventDB 初始化: {db_path}")

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS market_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                type TEXT NOT NULL,
                impact TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                timestamp_ms INTEGER,
                UNIQUE(date, title)
            )
        """)
        self._conn.commit()

    def _load_presets(self) -> None:
        """加载预置事件（跳过已存在的）。"""
        for event in PRESET_EVENTS:
            ts = int(datetime.strptime(event["date"], "%Y-%m-%d").timestamp() * 1000)
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO market_events (date, type, impact, title, description, timestamp_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (event["date"], event["type"], event["impact"], event["title"], event.get("desc", ""), ts),
                )
            except sqlite3.IntegrityError:
                pass
        self._conn.commit()

    def add_event(
        self, date: str, event_type: str, impact: str, title: str, description: str = ""
    ) -> None:
        """添加事件。"""
        ts = int(datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
        self._conn.execute(
            "INSERT OR REPLACE INTO market_events (date, type, impact, title, description, timestamp_ms) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, event_type, impact, title, description, ts),
        )
        self._conn.commit()
        logger.info(f"事件添加: [{date}] {title}")

    def get_events(
        self, event_type: Optional[str] = None, impact: Optional[str] = None
    ) -> pd.DataFrame:
        """查询事件。"""
        query = "SELECT * FROM market_events WHERE 1=1"
        params: list = []
        if event_type:
            query += " AND type=?"
            params.append(event_type)
        if impact:
            query += " AND impact=?"
            params.append(impact)
        query += " ORDER BY date"
        return pd.read_sql(query, self._conn, params=params)

    def get_event_windows(self, price: pd.Series, days_before: int = 7, days_after: int = 14) -> list[dict]:
        """获取每个事件前后的价格窗口。"""
        events = self.get_events()
        windows = []
        for _, event in events.iterrows():
            event_ts = event["timestamp_ms"]
            before_ts = event_ts - days_before * 24 * 3600 * 1000
            after_ts = event_ts + days_after * 24 * 3600 * 1000

            mask = (price.index >= pd.Timestamp(before_ts, unit="ms", tz="UTC")) & \
                   (price.index <= pd.Timestamp(after_ts, unit="ms", tz="UTC"))
            window = price[mask]

            if len(window) > 10:
                windows.append({
                    "title": event["title"],
                    "type": event["type"],
                    "impact": event["impact"],
                    "date": event["date"],
                    "price_window": window,
                })
        return windows

    def close(self) -> None:
        self._conn.close()
