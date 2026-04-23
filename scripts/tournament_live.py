"""淘汰赛实盘：32 个策略同时跑 paper trade。

每 5 分钟：
1. 拉最新 5m 数据
2. 32 个策略各自生成信号
3. 记录每个策略的虚拟交易
4. 统计每个策略的实时 P&L

每轮（用户指定间隔）：
1. 排名 32 个策略
2. 淘汰后 8（但 0 交易的不淘汰）
3. 改进前 8 产生变异体
4. 回到 32 继续跑
"""

import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.strategies.minute_swing import minute_swing_signal
from src.strategies.minute_swing_dual import minute_swing_dual_signal
from src.strategies.intraday_momentum import intraday_momentum_signal
from src.strategies.extreme_reversal import extreme_reversal_signal

COINS = ["ETH/USDT", "SOL/USDT", "NEAR/USDT", "ARB/USDT"]
INIT_CASH = 250.0  # $50 x 5x


def get_func(name):
    if "intraday" in name.lower():
        return intraday_momentum_signal
    elif "dual" in name.lower():
        return minute_swing_dual_signal
    elif "extreme" in name.lower():
        return extreme_reversal_signal
    else:
        return minute_swing_signal


def load_strategies():
    """从 tournament.json 加载最新 32 个策略。"""
    path = Path("reports/tournament.json")
    if not path.exists():
        return []
    data = json.load(open(path))
    # 找最新一代
    latest_key = list(data.keys())[-1]
    strats = data[latest_key]
    return [(s["name"], s["params"]) for s in strats]


class MultiStrategyTracker:
    """追踪 32 个策略的虚拟交易。"""

    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT, symbol TEXT, action TEXT,
                price REAL, timestamp TEXT, pnl_pct REAL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_positions (
                strategy TEXT, symbol TEXT,
                entry_price REAL, entry_time TEXT,
                PRIMARY KEY (strategy, symbol)
            )
        """)
        self._conn.commit()
        self._trade_counts = {}

    def has_position(self, strategy, symbol):
        r = self._conn.execute(
            "SELECT 1 FROM strategy_positions WHERE strategy=? AND symbol=?",
            (strategy, symbol)
        ).fetchone()
        return r is not None

    def enter(self, strategy, symbol, price):
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO strategy_positions VALUES (?,?,?,?)",
            (strategy, symbol, price, now)
        )
        self._conn.execute(
            "INSERT INTO strategy_trades (strategy,symbol,action,price,timestamp,pnl_pct) VALUES (?,?,?,?,?,?)",
            (strategy, symbol, "ENTRY", price, now, 0)
        )
        self._conn.commit()
        self._trade_counts[strategy] = self._trade_counts.get(strategy, 0) + 1

    def exit(self, strategy, symbol, price):
        pos = self._conn.execute(
            "SELECT entry_price FROM strategy_positions WHERE strategy=? AND symbol=?",
            (strategy, symbol)
        ).fetchone()
        if not pos:
            return 0
        pnl = (price - pos[0]) / pos[0] * 100
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO strategy_trades (strategy,symbol,action,price,timestamp,pnl_pct) VALUES (?,?,?,?,?,?)",
            (strategy, symbol, "EXIT", price, now, pnl)
        )
        self._conn.execute(
            "DELETE FROM strategy_positions WHERE strategy=? AND symbol=?",
            (strategy, symbol)
        )
        self._conn.commit()
        return pnl

    def get_rankings(self):
        """按总 P&L 排名所有策略。"""
        rows = self._conn.execute(
            "SELECT strategy, COUNT(*) as trades, SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins, "
            "SUM(pnl_pct) as total_pnl FROM strategy_trades WHERE action='EXIT' GROUP BY strategy"
        ).fetchall()
        results = []
        for name, trades, wins, pnl in rows:
            results.append({
                "name": name, "trades": trades,
                "wins": wins, "total_pnl": round(pnl, 2),
                "win_rate": round(wins / trades * 100, 0) if trades > 0 else 0
            })
        results.sort(key=lambda x: x["total_pnl"], reverse=True)
        return results

    def get_trade_count(self, strategy):
        return self._trade_counts.get(strategy, 0)

    def total_active_trades(self):
        r = self._conn.execute("SELECT COUNT(*) FROM strategy_positions").fetchone()
        return r[0] if r else 0


async def scan_all_strategies(tracker, strategies):
    """一次扫描：32 个策略 x 4 个币种。"""
    settings = get_settings()

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        # 拉数据（所有策略共用）
        price_data = {}
        for symbol in COINS:
            try:
                candles = await client.fetch_ohlcv_range(
                    symbol, timeframe="5m",
                    since=int(time.time() * 1000) - 300 * 5 * 60 * 1000,
                )
                if len(candles) >= 200:
                    df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                    price_data[symbol] = df.set_index("datetime")["close"]
            except Exception:
                pass

        if not price_data:
            return

        # 32 个策略各自生成信号
        entries_count = 0
        exits_count = 0

        for name, params in strategies:
            func = get_func(name)
            for symbol, price in price_data.items():
                try:
                    e, x = func(price, **params)
                    current = price.iloc[-1]
                    recent_entry = e.iloc[-3:].any()
                    recent_exit = x.iloc[-3:].any()

                    if recent_entry and not tracker.has_position(name, symbol):
                        tracker.enter(name, symbol, current)
                        entries_count += 1
                    elif recent_exit and tracker.has_position(name, symbol):
                        pnl = tracker.exit(name, symbol, current)
                        exits_count += 1
                except Exception:
                    pass

        now = time.strftime("%H:%M:%S")
        active = tracker.total_active_trades()
        logger.info(
            f"[{now}] 32策略扫描 | 新入场:{entries_count} 出场:{exits_count} | 持仓:{active}"
        )


async def main():
    strategies = load_strategies()
    if not strategies:
        logger.error("无策略数据，先跑 tournament.py")
        return

    logger.info(f"淘汰赛实盘启动 | {len(strategies)} 个策略 x {len(COINS)} 币种")

    db_path = Path("data") / "tournament_live.sqlite"
    db_path.parent.mkdir(exist_ok=True)
    tracker = MultiStrategyTracker(db_path)

    # 解析时长
    duration_min = 0
    for arg in sys.argv:
        if arg.startswith("--duration"):
            duration_min = int(arg.split("=")[1]) if "=" in arg else int(sys.argv[sys.argv.index(arg) + 1])

    start = time.time()
    scan_count = 0

    while True:
        scan_count += 1
        await scan_all_strategies(tracker, strategies)

        # 每 10 次扫描打印排名
        if scan_count % 10 == 0:
            rankings = tracker.get_rankings()
            if rankings:
                logger.info(f"\n--- 实盘排名 (前 5) ---")
                for r in rankings[:5]:
                    logger.info(f"  {r['name']:30s} | trades:{r['trades']} pnl:{r['total_pnl']:+.2f}%")

        # 检查时长
        elapsed = (time.time() - start) / 60
        if duration_min and elapsed >= duration_min:
            logger.info(f"\n{duration_min} 分钟到，停止。")
            break

        await asyncio.sleep(300)

    # 保存最终结果
    rankings = tracker.get_rankings()
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_min": round(elapsed, 1),
        "scans": scan_count,
        "rankings": rankings,
        "total_strategies": len(strategies),
    }
    with open("reports/tournament_live_result.json", "w") as f:
        json.dump(result, f, indent=2)
    logger.info(f"结果保存到 reports/tournament_live_result.json")


if __name__ == "__main__":
    asyncio.run(main())
