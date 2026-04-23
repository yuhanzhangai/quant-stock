"""TSLA 新闻事件驱动 Paper Trader。

策略：纯动量跟随（迭代验证最优）
- $50 本金 × 10x 杠杆 = $500 仓位
- 事件后 4h 观察反应方向
- 跟随方向做多/做空
- 止盈 8% / 止损 2% / 最长持仓 96h
- 每 60 秒扫描一次

运行：
    python scripts/tsla_paper_trader.py
"""

import asyncio
import io
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ccxt.async_support as ccxt_async
import pandas as pd
from loguru import logger

from src.news.tsla_news_fetcher import (
    fetch_google_news_rss,
    get_sentiment_summary,
)

# =========================================================================
# 配置
# =========================================================================
SYMBOL = "TSLA/USDT:USDT"
SYMBOL_OKX = "TSLA-USDT-SWAP"
MARGIN_USD = 50.0
LEVERAGE = 10
POSITION_USD = MARGIN_USD * LEVERAGE

# 策略参数（迭代最优）
REACTION_HOURS = 4
HOLD_HOURS = 96
MOMENTUM_THRESHOLD = 0.3  # %
STOP_PCT = 2.0
TAKE_PROFIT_PCT = 8.0

SCAN_INTERVAL = 60  # 秒

# 已知事件（持续更新）
KNOWN_EVENTS = [
    {"date": "2026-04-22", "title": "Q1 2026 财报发布", "type": "earnings"},
    {"date": "2026-04-14", "title": "市场反弹 / 风险回升", "type": "macro"},
    {"date": "2026-04-09", "title": "关税 90 天暂停", "type": "regulatory"},
    {"date": "2026-04-07", "title": "全球暴跌 / 关税恐慌", "type": "macro"},
    {"date": "2026-04-02", "title": "Trump 全面关税升级", "type": "regulatory"},
    {"date": "2026-04-01", "title": "Q1 交付低于预期", "type": "earnings"},
]


# =========================================================================
# 数据获取
# =========================================================================
async def fetch_latest_candles(timeframe: str = "1h", limit: int = 200) -> pd.DataFrame:
    """从 OKX 拉取最新 K 线。"""
    ex = ccxt_async.okx({"enableRateLimit": True})
    try:
        candles = await ex.fetch_ohlcv(SYMBOL, timeframe, limit=limit)
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        return df
    finally:
        await ex.close()


async def fetch_current_price() -> float:
    """获取当前价格。"""
    ex = ccxt_async.okx({"enableRateLimit": True})
    try:
        ticker = await ex.fetch_ticker(SYMBOL)
        return ticker["last"]
    finally:
        await ex.close()


# =========================================================================
# Paper Trading 引擎
# =========================================================================
class TslaPaperTrader:
    """TSLA Paper Trader - SQLite 持久化。"""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._create_tables()
        self._stats = {"total_pnl_usd": 0.0, "total_pnl_pct": 0.0, "wins": 0, "losses": 0}
        self._load_stats()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                event_title TEXT,
                event_date TEXT,
                pnl_pct REAL DEFAULT 0,
                pnl_usd REAL DEFAULT 0,
                exit_reason TEXT,
                leverage INTEGER DEFAULT 10,
                margin_usd REAL DEFAULT 50
            );
            CREATE TABLE IF NOT EXISTS position (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                event_title TEXT,
                event_date TEXT,
                peak_price REAL,
                trough_price REAL,
                bars_held INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_pnl_usd REAL DEFAULT 0,
                total_pnl_pct REAL DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_trades INTEGER DEFAULT 0
            );
        """)
        self._conn.commit()

    def _load_stats(self) -> None:
        row = self._conn.execute("SELECT * FROM stats WHERE id=1").fetchone()
        if row:
            self._stats = {
                "total_pnl_usd": row[1], "total_pnl_pct": row[2],
                "wins": row[3], "losses": row[4], "total_trades": row[5] if len(row) > 5 else 0,
            }

    def _save_stats(self) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO stats (id, total_pnl_usd, total_pnl_pct, wins, losses, total_trades) VALUES (1,?,?,?,?,?)",
            (self._stats["total_pnl_usd"], self._stats["total_pnl_pct"],
             self._stats["wins"], self._stats["losses"],
             self._stats.get("total_trades", 0)),
        )
        self._conn.commit()

    def has_position(self) -> bool:
        return self._conn.execute("SELECT 1 FROM position WHERE id=1").fetchone() is not None

    def get_position(self) -> dict | None:
        row = self._conn.execute("SELECT * FROM position WHERE id=1").fetchone()
        if not row:
            return None
        return {
            "side": row[1], "entry_price": row[2], "entry_time": row[3],
            "event_title": row[4], "event_date": row[5],
            "peak_price": row[6], "trough_price": row[7], "bars_held": row[8],
        }

    def open_position(self, side: str, price: float, event_title: str, event_date: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO position (id, side, entry_price, entry_time, event_title, event_date, peak_price, trough_price, bars_held) VALUES (1,?,?,?,?,?,?,?,0)",
            (side, price, now, event_title, event_date, price, price),
        )
        self._conn.execute(
            "INSERT INTO trades (timestamp, action, side, price, event_title, event_date, leverage, margin_usd) VALUES (?,?,?,?,?,?,?,?)",
            (now, "OPEN", side, price, event_title, event_date, LEVERAGE, MARGIN_USD),
        )
        self._conn.commit()

        lev_size = MARGIN_USD * LEVERAGE
        logger.info(
            f"PAPER OPEN | {side} TSLA @ ${price:.2f} | "
            f"仓位: ${lev_size:.0f} (${MARGIN_USD}x{LEVERAGE}) | "
            f"事件: {event_title}"
        )

    def close_position(self, price: float, reason: str) -> dict:
        pos = self.get_position()
        if not pos:
            return {}

        entry = pos["entry_price"]
        side = pos["side"]

        if side == "LONG":
            pnl_pct = (price - entry) / entry * 100
        else:
            pnl_pct = (entry - price) / entry * 100

        pnl_usd = MARGIN_USD * LEVERAGE * pnl_pct / 100
        leveraged_pnl_pct = pnl_pct * LEVERAGE

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO trades (timestamp, action, side, price, event_title, event_date, pnl_pct, pnl_usd, exit_reason, leverage, margin_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now, "CLOSE", side, price, pos["event_title"], pos["event_date"],
             pnl_pct, pnl_usd, reason, LEVERAGE, MARGIN_USD),
        )
        self._conn.execute("DELETE FROM position WHERE id=1")

        self._stats["total_pnl_usd"] += pnl_usd
        self._stats["total_pnl_pct"] += leveraged_pnl_pct
        self._stats["total_trades"] = self._stats.get("total_trades", 0) + 1
        if pnl_pct > 0:
            self._stats["wins"] += 1
        else:
            self._stats["losses"] += 1
        self._save_stats()
        self._conn.commit()

        icon = "W" if pnl_pct > 0 else "L"
        logger.info(
            f"PAPER CLOSE [{icon}] | {side} TSLA @ ${price:.2f} | "
            f"P&L: {pnl_pct:+.2f}% (x{LEVERAGE}={leveraged_pnl_pct:+.1f}%) ${pnl_usd:+.2f} | "
            f"{reason} | 事件: {pos['event_title']}"
        )

        return {
            "side": side, "entry": entry, "exit": price,
            "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
            "leveraged_pnl_pct": leveraged_pnl_pct, "reason": reason,
        }

    def update_position(self, current_price: float) -> None:
        """更新持仓的峰值/谷值和持仓时间。"""
        pos = self.get_position()
        if not pos:
            return
        peak = max(pos["peak_price"] or current_price, current_price)
        trough = min(pos["trough_price"] or current_price, current_price)
        bars = pos["bars_held"] + 1
        self._conn.execute(
            "UPDATE position SET peak_price=?, trough_price=?, bars_held=? WHERE id=1",
            (peak, trough, bars),
        )
        self._conn.commit()

    def get_stats_display(self) -> str:
        wins = self._stats["wins"]
        losses = self._stats["losses"]
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0
        balance = MARGIN_USD + self._stats["total_pnl_usd"]
        return (
            f"累计 P&L: ${self._stats['total_pnl_usd']:+.2f} "
            f"({self._stats['total_pnl_pct']:+.1f}%) | "
            f"余额: ${balance:.2f} | "
            f"胜率: {wr:.0f}% ({wins}W/{losses}L)"
        )


# =========================================================================
# 信号扫描
# =========================================================================
def check_event_signal(
    df: pd.DataFrame,
    events: list[dict],
) -> dict | None:
    """检查是否有事件触发交易信号。

    Returns:
        信号字典 或 None
    """
    now = pd.Timestamp.now(tz="UTC")
    price = df["close"]

    for event in events:
        event_ts = pd.Timestamp(event["date"], tz="UTC")
        hours_since = (now - event_ts).total_seconds() / 3600

        # 只看 reaction 窗口内的事件（事件后 4~96h）
        if hours_since < REACTION_HOURS or hours_since > HOLD_HOURS:
            continue

        # 找事件后第一根 K 线
        mask = df.index >= event_ts
        if not mask.any():
            continue

        first_bar = mask.argmax()
        reaction_end = min(first_bar + REACTION_HOURS, len(price) - 1)
        if reaction_end <= first_bar:
            continue

        entry_price = price.iloc[first_bar]
        reaction_price = price.iloc[reaction_end]
        change_pct = (reaction_price - entry_price) / entry_price * 100

        if abs(change_pct) < MOMENTUM_THRESHOLD:
            continue

        # 刚好在 reaction 窗口结束附近（允许 2h 误差）
        if REACTION_HOURS <= hours_since <= REACTION_HOURS + 2:
            side = "LONG" if change_pct > 0 else "SHORT"
            return {
                "side": side,
                "price": float(price.iloc[-1]),
                "event_title": event["title"],
                "event_date": event["date"],
                "reaction_pct": change_pct,
            }

    return None


def check_exit_signal(pos: dict, current_price: float) -> str | None:
    """检查是否该平仓。

    Returns:
        退出原因 或 None
    """
    entry = pos["entry_price"]
    side = pos["side"]
    bars = pos["bars_held"]

    if side == "LONG":
        pnl_pct = (current_price - entry) / entry * 100
    else:
        pnl_pct = (entry - current_price) / entry * 100

    # 爆仓检查
    if pnl_pct * LEVERAGE <= -100:
        return "爆仓"

    # 止盈
    if pnl_pct >= TAKE_PROFIT_PCT:
        return f"止盈 {pnl_pct:+.2f}%"

    # 止损
    if pnl_pct <= -STOP_PCT:
        return f"止损 {pnl_pct:+.2f}%"

    # 到期
    if bars >= HOLD_HOURS:
        return f"到期 ({bars}h)"

    return None


# =========================================================================
# 主循环
# =========================================================================
async def run_paper_trader() -> None:
    """Paper Trading 主循环。"""
    db_path = Path("data/tsla_paper_trades.sqlite")
    db_path.parent.mkdir(exist_ok=True)
    trader = TslaPaperTrader(db_path)

    cycle = 0
    logger.info("=" * 70)
    logger.info("TSLA Paper Trader 启动")
    logger.info(f"策略: 新闻事件纯动量跟随 | Alpha: +6.17% (验证)")
    logger.info(f"本金: ${MARGIN_USD} | 杠杆: {LEVERAGE}x | 仓位: ${POSITION_USD}")
    logger.info(f"参数: 反应{REACTION_HOURS}h / 持仓{HOLD_HOURS}h / 阈值{MOMENTUM_THRESHOLD}% / TP{TAKE_PROFIT_PCT}% / SL{STOP_PCT}%")
    logger.info(f"扫描间隔: {SCAN_INTERVAL}s")
    logger.info("=" * 70)

    while True:
        cycle += 1
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

        try:
            # 拉最新数据
            df = await fetch_latest_candles("1h", limit=200)
            if df.empty:
                logger.warning("数据为空，跳过")
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            current_price = float(df["close"].iloc[-1])
            price_change_1h = (df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100 if len(df) > 1 else 0

            # ---- 有持仓：检查是否该平仓 ----
            if trader.has_position():
                pos = trader.get_position()
                trader.update_position(current_price)

                # 计算浮动盈亏
                if pos["side"] == "LONG":
                    float_pnl = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
                else:
                    float_pnl = (pos["entry_price"] - current_price) / pos["entry_price"] * 100

                lev_pnl = float_pnl * LEVERAGE
                bars = pos["bars_held"]

                exit_reason = check_exit_signal(pos, current_price)
                if exit_reason:
                    trader.close_position(current_price, exit_reason)
                else:
                    if cycle % 5 == 0:  # 每 5 分钟打印一次
                        print(
                            f"  [{now_str}] #{cycle} | TSLA ${current_price:.2f} ({price_change_1h:+.2f}% 1h) | "
                            f"持仓 {pos['side']} ${pos['entry_price']:.2f} | "
                            f"浮动 {float_pnl:+.2f}% (x{LEVERAGE}={lev_pnl:+.1f}%) | "
                            f"{bars}h/{HOLD_HOURS}h | {trader.get_stats_display()}"
                        )

            # ---- 无持仓：检查是否有新信号 ----
            else:
                signal = check_event_signal(df, KNOWN_EVENTS)

                if signal:
                    trader.open_position(
                        signal["side"], signal["price"],
                        signal["event_title"], signal["event_date"],
                    )
                else:
                    if cycle % 10 == 0:  # 每 10 分钟打印一次
                        # 检查新闻
                        try:
                            news = fetch_google_news_rss("Tesla TSLA", max_items=10)
                            summary = get_sentiment_summary(news)
                            news_str = f"新闻: {summary['overall']} ({summary['avg_score']:+.3f})"
                        except Exception:
                            news_str = "新闻: N/A"

                        print(
                            f"  [{now_str}] #{cycle} | TSLA ${current_price:.2f} ({price_change_1h:+.2f}% 1h) | "
                            f"等待信号 | {news_str} | {trader.get_stats_display()}"
                        )

        except Exception as e:
            logger.error(f"扫描异常: {e}")

        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(run_paper_trader())
    except KeyboardInterrupt:
        print("\nPaper Trader 已停止。")
