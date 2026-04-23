"""做空策略 Paper Trader：session_filter 实时模拟盘。

每 5 分钟：
1. 拉最新 5m K 线数据（最近 300 根 = 25 小时）
2. 用 session_filter 冠军参数生成做空信号
3. 检查时段过滤（UTC 20-13，排除美盘下午）
4. 记录入场/出场到 SQLite
5. 追踪每笔做空交易的虚拟 P&L

冠军参数: session UTC20-13, fast=84, slow=180, gap=288
           stop=7.0%, tp=12%, trail=1.0%

运行: uv run python scripts/short_paper_trader.py
     uv run python scripts/short_paper_trader.py --once   # 单次扫描
     uv run python scripts/short_paper_trader.py --duration=60  # 跑60分钟
"""

import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
from src.exchange.ccxt_client import CCXTClient
from src.strategies.short_session_filter import ShortSessionFilterStrategy

# 冠军参数
STRATEGY_PARAMS = {
    "session_start": 20,
    "session_end": 13,
    "fast_ma": 84,
    "slow_ma": 180,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "min_gap": 288,
    "stop_pct": 7.0,
    "take_profit_pct": 12.0,
    "trail_pct": 1.0,
}

COINS = {
    "ETH/USDT": "ETH",
    "SOL/USDT": "SOL",
    "NEAR/USDT": "NEAR",
    "ARB/USDT": "ARB",
    "DOT/USDT": "DOT",
    "OP/USDT": "OP",
    "SUI/USDT": "SUI",
    "ATOM/USDT": "ATOM",
    "PEPE/USDT": "PEPE",
    "FIL/USDT": "FIL",
}

LEVERAGE = 5
CAPITAL_PER_COIN = 5.0  # $50 / 10 coins = $5 each


class ShortPaperTrader:
    """做空模拟交易器。"""

    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._create_tables()
        self._strategy = ShortSessionFilterStrategy()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS short_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, action TEXT, price REAL,
                timestamp TEXT, pnl_pct REAL,
                position_size REAL, leverage INTEGER,
                note TEXT
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS short_positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL, entry_time TEXT,
                trough_price REAL
            )
        """)
        self._conn.commit()

    def has_position(self, symbol: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM short_positions WHERE symbol=?", (symbol,)
        ).fetchone()
        return row is not None

    def get_position(self, symbol: str) -> dict | None:
        row = self._conn.execute(
            "SELECT entry_price, entry_time, trough_price FROM short_positions WHERE symbol=?",
            (symbol,),
        ).fetchone()
        if row:
            return {"entry_price": row[0], "entry_time": row[1], "trough_price": row[2]}
        return None

    def record_entry(self, symbol: str, price: float, note: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO short_trades (symbol,action,price,timestamp,pnl_pct,position_size,leverage,note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (symbol, "SHORT_ENTRY", price, now, 0.0, CAPITAL_PER_COIN * LEVERAGE, LEVERAGE, note),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO short_positions (symbol,entry_price,entry_time,trough_price) "
            "VALUES (?,?,?,?)",
            (symbol, price, now, price),
        )
        self._conn.commit()
        logger.info(
            f"SHORT ENTRY | {symbol} @ ${price:,.4f} | "
            f"size: ${CAPITAL_PER_COIN * LEVERAGE:,.2f} | {note}"
        )

    def update_trough(self, symbol: str, current_price: float) -> None:
        """更新最低价（用于 trailing stop 计算）。"""
        pos = self.get_position(symbol)
        if pos and current_price < pos["trough_price"]:
            self._conn.execute(
                "UPDATE short_positions SET trough_price=? WHERE symbol=?",
                (current_price, symbol),
            )
            self._conn.commit()

    def record_exit(self, symbol: str, price: float, reason: str = "") -> None:
        pos = self.get_position(symbol)
        if not pos:
            return

        entry_price = pos["entry_price"]
        # 做空P&L: 价格下跌=盈利
        pnl_pct = (entry_price - price) / entry_price * 100
        pnl_usd = CAPITAL_PER_COIN * LEVERAGE * pnl_pct / 100

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO short_trades (symbol,action,price,timestamp,pnl_pct,position_size,leverage,note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (symbol, "SHORT_EXIT", price, now, pnl_pct, 0, LEVERAGE, reason),
        )
        self._conn.execute("DELETE FROM short_positions WHERE symbol=?", (symbol,))
        self._conn.commit()

        emoji = "+" if pnl_pct > 0 else ""
        logger.info(
            f"SHORT EXIT  | {symbol} @ ${price:,.4f} | "
            f"P&L: {emoji}{pnl_pct:.2f}% (${emoji}{pnl_usd:.2f}) | {reason}"
        )

    def check_exit_conditions(self, symbol: str, current_price: float, ma_fast: float, ma_slow: float) -> str | None:
        """检查做空出场条件。返回出场原因或 None。"""
        pos = self.get_position(symbol)
        if not pos:
            return None

        entry_price = pos["entry_price"]
        trough_price = pos["trough_price"]

        # 做空 P&L
        pnl_pct = (entry_price - current_price) / entry_price * 100
        bounce_pct = (current_price - trough_price) / trough_price * 100 if trough_price > 0 else 0

        # 安全网止损
        if current_price > entry_price * (1 + STRATEGY_PARAMS["stop_pct"] / 100):
            return f"STOP_LOSS ({pnl_pct:+.2f}%)"

        # 止盈
        if pnl_pct > STRATEGY_PARAMS["take_profit_pct"]:
            return f"TAKE_PROFIT ({pnl_pct:+.2f}%)"

        # Trailing stop
        if pnl_pct > 2.0 and bounce_pct > STRATEGY_PARAMS["trail_pct"]:
            return f"TRAIL_STOP ({pnl_pct:+.2f}%, bounce {bounce_pct:.1f}%)"

        # 趋势反转
        if ma_fast > ma_slow:
            return f"TREND_REVERSAL ({pnl_pct:+.2f}%)"

        return None

    def get_summary(self) -> str:
        trades = self._conn.execute("SELECT COUNT(*) FROM short_trades").fetchone()[0]
        positions = self._conn.execute("SELECT COUNT(*) FROM short_positions").fetchone()[0]
        # 计算总P&L
        exits = self._conn.execute(
            "SELECT SUM(pnl_pct) FROM short_trades WHERE action='SHORT_EXIT'"
        ).fetchone()[0]
        total_pnl = exits or 0.0
        return f"Trades: {trades} | Open: {positions} | Total P&L: {total_pnl:+.2f}%"

    def get_open_positions_display(self) -> str:
        rows = self._conn.execute(
            "SELECT symbol, entry_price, entry_time FROM short_positions"
        ).fetchall()
        if not rows:
            return "  No open positions"
        lines = []
        for symbol, ep, et in rows:
            lines.append(f"  {symbol} SHORT @ ${ep:,.4f} ({et[:16]})")
        return "\n".join(lines)


async def scan_and_trade(trader: ShortPaperTrader) -> None:
    """一次扫描周期。"""
    settings = get_settings()

    async with CCXTClient(
        api_key=settings.okx_api_key,
        api_secret=settings.okx_api_secret,
        passphrase=settings.okx_passphrase,
    ) as client:
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        in_session = hour >= STRATEGY_PARAMS["session_start"] or hour < STRATEGY_PARAMS["session_end"]

        logger.info(
            f"\n--- Short scan {now_utc.strftime('%H:%M:%S UTC')} | "
            f"Session: {'IN (可入场)' if in_session else 'OUT (仅出场)'} ---"
        )

        for symbol, coin in COINS.items():
            try:
                # 拉最近 300 根 5m K 线（25 小时）
                since_ms = int(time.time() * 1000) - 300 * 5 * 60 * 1000
                candles = await client.fetch_ohlcv_range(symbol, timeframe="5m", since=since_ms)

                if len(candles) < 200:
                    logger.warning(f"{symbol}: 数据不足 ({len(candles)} 根)")
                    continue

                df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "close", "v"])
                df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                price = df.set_index("datetime")["close"]
                current = float(price.iloc[-1])

                # 计算 MA（用于出场判断）
                ma_fast = float(price.rolling(window=STRATEGY_PARAMS["fast_ma"]).mean().iloc[-1])
                ma_slow = float(price.rolling(window=STRATEGY_PARAMS["slow_ma"]).mean().iloc[-1])

                # 更新持仓最低价
                if trader.has_position(symbol):
                    trader.update_trough(symbol, current)

                    # 检查出场条件（不受时段限制，随时可出场）
                    exit_reason = trader.check_exit_conditions(symbol, current, ma_fast, ma_slow)
                    if exit_reason:
                        trader.record_exit(symbol, current, exit_reason)
                        continue

                    # 显示持仓状态
                    pos = trader.get_position(symbol)
                    if pos:
                        pnl = (pos["entry_price"] - current) / pos["entry_price"] * 100
                        logger.debug(f"  {symbol} HOLDING | P&L: {pnl:+.2f}% | trough: ${pos['trough_price']:,.4f}")

                elif in_session:
                    # 生成入场信号
                    entries, exits = trader._strategy.generate_signals(price, **STRATEGY_PARAMS)

                    # 检查最近 3 根是否有入场信号
                    if entries.iloc[-3:].any():
                        trend_info = f"MA{STRATEGY_PARAMS['fast_ma']}={ma_fast:.2f} MA{STRATEGY_PARAMS['slow_ma']}={ma_slow:.2f}"
                        trader.record_entry(symbol, current, f"session_filter | {trend_info}")

            except Exception as e:
                logger.error(f"{symbol}: {e}")

        logger.info(f"Status: {trader.get_summary()}")


async def main() -> None:
    db_path = Path("data") / "short_paper_trades.sqlite"
    db_path.parent.mkdir(exist_ok=True)
    trader = ShortPaperTrader(db_path)

    duration_min = 0
    for arg in sys.argv:
        if arg.startswith("--duration"):
            duration_min = int(arg.split("=")[1]) if "=" in arg else int(sys.argv[sys.argv.index(arg) + 1])

    start_time = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("  SHORT Paper Trader | session_filter 冠军 (Sharpe +3.14)")
    logger.info(f"  时段: UTC {STRATEGY_PARAMS['session_start']}:00 ~ {STRATEGY_PARAMS['session_end']}:00")
    logger.info(f"  参数: fast={STRATEGY_PARAMS['fast_ma']} slow={STRATEGY_PARAMS['slow_ma']} gap={STRATEGY_PARAMS['min_gap']}")
    logger.info(f"  出场: trail={STRATEGY_PARAMS['trail_pct']}% stop={STRATEGY_PARAMS['stop_pct']}% tp={STRATEGY_PARAMS['take_profit_pct']}%")
    logger.info(f"  币种: {list(COINS.keys())}")
    logger.info(f"  资金: ${CAPITAL_PER_COIN * len(COINS)} total, ${CAPITAL_PER_COIN}/coin x {LEVERAGE}x")
    logger.info("=" * 60)

    if "--once" in sys.argv:
        await scan_and_trade(trader)
    else:
        scan_count = 0
        while True:
            scan_count += 1
            await scan_and_trade(trader)

            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() / 60
            if duration_min and elapsed >= duration_min:
                logger.info(f"\n时长 {duration_min} 分钟已到，自动停止。")
                break

            # 每 5 分钟扫描一次
            await asyncio.sleep(300)

    # 最终报告
    logger.info("\n" + "=" * 60)
    logger.info("  最终报告")
    logger.info("=" * 60)
    logger.info(trader.get_summary())
    logger.info("Open positions:\n" + trader.get_open_positions_display())


if __name__ == "__main__":
    asyncio.run(main())
