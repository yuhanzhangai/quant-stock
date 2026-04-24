"""标准化 Paper Trading Session 管理。

每个 session 输出:
data/research/paper_sessions/session_id=xxx/
  config.yml
  signals.parquet
  accepted_trades.parquet
  rejected_signals.parquet
  fills.parquet
  equity.parquet
  daily_summary.json
  final_report.json
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml
from loguru import logger

SESSION_DIR = Path("data/research/paper_sessions")
DB_PATH = Path("data/meta/research.duckdb")


@dataclass
class PaperSignal:
    """记录每个信号（无论是否交易）。"""

    signal_id: str
    ts: str
    strategy_name: str
    strategy_version: str
    symbol: str
    timeframe: str
    side: str  # long | short
    confidence: str  # HIGH | MED | LOW
    price_ref: float
    entry_reason: str
    params_hash: str = ""
    data_version: str = ""


@dataclass
class RejectedSignal:
    """被拒绝的信号。"""

    signal_id: str
    ts: str
    symbol: str
    strategy_name: str
    reject_reason: str
    details: dict = field(default_factory=dict)


@dataclass
class PaperFill:
    """模拟成交。"""

    fill_id: str
    signal_id: str
    planned_price: float
    simulated_fill_price: float
    slippage: float
    fee: float
    notional: float
    size: float
    fill_ts: str
    fill_model: str = "fixed_bps"


@dataclass
class PaperTrade:
    """完成的交易。"""

    trade_id: str
    signal_id: str
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    side: str
    size: float
    gross_pnl: float
    fee: float
    slippage: float
    net_pnl: float
    return_pct: float
    mae_pct: float
    mfe_pct: float
    holding_bars: int
    exit_reason: str  # stop_loss | take_profit | trailing_stop | time_stop | opposite_signal | risk_exit | manual_close


class PaperSession:
    """Paper Trading 会话管理器。"""

    def __init__(
        self,
        strategy_name: str,
        strategy_version: str = "1.0.0",
        symbol: str = "ETH-USDT",
        timeframe: str = "5m",
        initial_equity: float = 50.0,
    ) -> None:
        """初始化 session。"""
        date = datetime.now(tz=UTC).strftime("%Y%m%d")
        sym = symbol.replace("-USDT", "").lower()
        self.session_id = f"{date}_{strategy_name}_{sym}_{uuid.uuid4().hex[:6]}"
        self.strategy_name = strategy_name
        self.strategy_version = strategy_version
        self.symbol = symbol
        self.timeframe = timeframe
        self.initial_equity = initial_equity
        self.equity = initial_equity

        self.signals: list[PaperSignal] = []
        self.rejected: list[RejectedSignal] = []
        self.fills: list[PaperFill] = []
        self.trades: list[PaperTrade] = []
        self.equity_curve: list[dict] = []

        self._status = "created"
        self._error_message = ""
        self._session_dir = SESSION_DIR / f"session_id={self.session_id}"
        self._session_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Paper session created: {self.session_id}")

    def __enter__(self) -> "PaperSession":
        """Context manager entry."""
        self._status = "running"
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: object) -> bool:
        """Context manager exit — auto finalize."""
        if exc_type is not None:
            self._status = "failed"
            self._error_message = str(exc_val)
            logger.error(f"Paper session failed: {exc_val}")
        else:
            self._status = "completed"
        self.save_all()
        return False  # don't suppress exceptions

    def record_signal(self, signal: PaperSignal) -> None:
        """记录信号。"""
        self.signals.append(signal)

    def record_rejection(self, rejection: RejectedSignal) -> None:
        """记录被拒绝的信号。"""
        self.rejected.append(rejection)

    def record_fill(self, fill: PaperFill) -> None:
        """记录成交。"""
        self.fills.append(fill)

    def record_trade(self, trade: PaperTrade) -> None:
        """记录完成的交易。"""
        self.trades.append(trade)
        self.equity += trade.net_pnl

    def record_equity(self, ts: str, equity: float) -> None:
        """记录权益快照。"""
        self.equity_curve.append({"ts": ts, "equity": equity})

    def save_config(self) -> None:
        """保存 session 配置。"""
        config = {
            "session_id": self.session_id,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "initial_equity": self.initial_equity,
            "created_at": datetime.now(tz=UTC).isoformat(),
        }
        with open(self._session_dir / "config.yml", "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True)

    def save_all(self) -> Path:
        """保存所有数据。"""
        self.save_config()

        # Signals
        if self.signals:
            data = [vars(s) for s in self.signals]
            pl.DataFrame(data).write_parquet(str(self._session_dir / "signals.parquet"))

        # Rejected
        if self.rejected:
            data = [
                {
                    "signal_id": r.signal_id,
                    "ts": r.ts,
                    "symbol": r.symbol,
                    "strategy_name": r.strategy_name,
                    "reject_reason": r.reject_reason,
                    "details": json.dumps(r.details),
                }
                for r in self.rejected
            ]
            pl.DataFrame(data).write_parquet(str(self._session_dir / "rejected_signals.parquet"))

        # Fills
        if self.fills:
            data = [vars(f) for f in self.fills]
            pl.DataFrame(data).write_parquet(str(self._session_dir / "fills.parquet"))

        # Trades
        if self.trades:
            data = [vars(t) for t in self.trades]
            pl.DataFrame(data).write_parquet(str(self._session_dir / "accepted_trades.parquet"))

        # Equity
        if self.equity_curve:
            pl.DataFrame(self.equity_curve).write_parquet(str(self._session_dir / "equity.parquet"))

        # Final report
        report = {
            "session_id": self.session_id,
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "initial_equity": self.initial_equity,
            "final_equity": round(self.equity, 2),
            "net_pnl": round(self.equity - self.initial_equity, 2),
            "total_signals": len(self.signals),
            "accepted_trades": len(self.trades),
            "rejected_signals": len(self.rejected),
            "created_at": datetime.now(tz=UTC).isoformat(),
        }
        with open(self._session_dir / "final_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        # Save to DB
        self._save_to_db(report)

        logger.info(f"Paper session saved: {self._session_dir}")
        return self._session_dir

    def _save_to_db(self, report: dict) -> None:
        """写入 paper_sessions 表。"""
        from src.research.db import connect_research_db

        conn = connect_research_db(required=True)
        conn.execute(
            """
            INSERT INTO paper_sessions
            (session_id, strategy_name, strategy_version, initial_equity,
             final_equity, total_signals, accepted_trades, rejected_signals,
             net_pnl, status, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
            """,
            [
                self.session_id,
                self.strategy_name,
                self.strategy_version,
                self.initial_equity,
                report["final_equity"],
                report["total_signals"],
                report["accepted_trades"],
                report["rejected_signals"],
                report["net_pnl"],
                self._status,
                self._error_message or None,
            ],
        )
        conn.close()


def compare_paper_vs_backtest(session_dir: Path, backtest_dir: Path) -> dict:
    """对比 paper trading 和 backtest 结果。"""
    result: dict[str, Any] = {"session_dir": str(session_dir), "backtest_dir": str(backtest_dir)}

    # Load paper report
    paper_report = session_dir / "final_report.json"
    if paper_report.exists():
        with open(paper_report, encoding="utf-8") as f:
            paper = json.load(f)
        result["paper"] = {
            "signals": paper.get("total_signals", 0),
            "trades": paper.get("accepted_trades", 0),
            "net_pnl": paper.get("net_pnl", 0),
        }

    # Load backtest metrics
    bt_metrics = backtest_dir / "metrics.json"
    if bt_metrics.exists():
        with open(bt_metrics, encoding="utf-8") as f:
            bt = json.load(f)
        result["backtest"] = {
            "trades": bt.get("trade_count", 0),
            "sharpe": bt.get("sharpe", 0),
            "net_return": bt.get("net_return", 0),
        }

    return result
