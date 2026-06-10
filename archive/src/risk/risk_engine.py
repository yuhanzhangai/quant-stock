"""风险引擎：信号过滤 + 交易拒绝。

不是每个信号都应该交易。风险引擎决定是否接受信号。

拒绝原因:
- expected_edge_too_low
- daily_loss_limit_reached
- max_exposure_reached
- data_delay
- cooldown_after_losses
- max_drawdown_reached
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from loguru import logger

CONFIG_PATH = Path("config/risk/small_account.yml")


@dataclass
class SignalDecision:
    """信号决策结果。"""

    accepted: bool
    reason: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class RiskState:
    """当前风险状态追踪。"""

    equity: float = 50.0
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    total_drawdown_pct: float = 0.0
    peak_equity: float = 50.0
    last_trade_bar: int = -999
    trades_today: int = 0
    rejected_signals: list = field(default_factory=list)


class RiskEngine:
    """风险引擎。"""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        """初始化风险引擎。"""
        self._config = self._load_config(config_path)
        self._state = RiskState(equity=self._config["account"]["initial_equity"])
        self._state.peak_equity = self._state.equity

    def check_signal(
        self,
        bar_idx: int = 0,
        expected_edge: float = 0.0,
        cost_per_trade: float = 0.001,
        data_delay_bars: int = 0,
    ) -> SignalDecision:
        """检查信号是否应该交易。"""
        risk = self._config["risk"]
        execution = self._config["execution"]

        # Check data delay
        if data_delay_bars > execution["block_if_latest_bar_delay_gt_bars"]:
            return self._reject("data_delay", {"delay_bars": data_delay_bars})

        # Check daily loss limit
        daily_loss_pct = abs(self._state.daily_pnl) / max(self._state.equity, 1)
        if self._state.daily_pnl < 0 and daily_loss_pct > risk["max_daily_loss_pct"]:
            return self._reject("daily_loss_limit_reached", {"daily_loss_pct": round(daily_loss_pct, 4)})

        # Check consecutive losses
        if self._state.consecutive_losses >= risk["max_consecutive_losses"]:
            cooldown = execution.get("cooldown_after_losses_bars", 288)
            if bar_idx - self._state.last_trade_bar < cooldown:
                return self._reject(
                    "cooldown_after_losses",
                    {"consecutive_losses": self._state.consecutive_losses, "cooldown_bars": cooldown},
                )

        # Check total drawdown
        if self._state.total_drawdown_pct < -risk["max_total_drawdown_pct"]:
            return self._reject("max_drawdown_reached", {"drawdown": round(self._state.total_drawdown_pct, 4)})

        # Check edge-to-cost ratio
        if cost_per_trade > 0:
            edge_ratio = expected_edge / cost_per_trade
            if edge_ratio < execution["min_expected_edge_to_cost_ratio"]:
                return self._reject(
                    "expected_edge_too_low",
                    {"edge": round(expected_edge, 6), "cost": round(cost_per_trade, 6), "ratio": round(edge_ratio, 2)},
                )

        return SignalDecision(accepted=True, reason="signal_accepted")

    def update_trade_result(self, pnl: float, bar_idx: int) -> None:
        """更新交易结果到风险状态。"""
        self._state.equity += pnl
        self._state.daily_pnl += pnl
        self._state.last_trade_bar = bar_idx
        self._state.trades_today += 1

        if pnl < 0:
            self._state.consecutive_losses += 1
        else:
            self._state.consecutive_losses = 0

        if self._state.equity > self._state.peak_equity:
            self._state.peak_equity = self._state.equity

        self._state.total_drawdown_pct = (
            (self._state.equity - self._state.peak_equity) / self._state.peak_equity
            if self._state.peak_equity > 0
            else 0
        )

    def reset_daily(self) -> None:
        """重置每日状态。"""
        self._state.daily_pnl = 0.0
        self._state.trades_today = 0

    @property
    def state(self) -> RiskState:
        """返回当前状态。"""
        return self._state

    @property
    def rejected_signals(self) -> list:
        """返回被拒绝的信号列表。"""
        return self._state.rejected_signals

    def _reject(self, reason: str, details: dict) -> SignalDecision:
        """记录拒绝决策。"""
        decision = SignalDecision(accepted=False, reason=reason, details=details)
        self._state.rejected_signals.append(
            {
                "reason": reason,
                "details": details,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )
        logger.debug(f"Signal rejected: {reason} | {details}")
        return decision

    def _load_config(self, path: Path) -> dict:
        """加载风险配置。"""
        if not path.exists():
            logger.warning(f"Risk config not found: {path}, using defaults")
            return {
                "account": {"initial_equity": 50, "max_leverage": 5, "max_effective_leverage": 2},
                "risk": {
                    "max_risk_per_trade_pct": 0.005,
                    "max_daily_loss_pct": 0.03,
                    "max_total_drawdown_pct": 0.25,
                    "max_consecutive_losses": 5,
                },
                "execution": {
                    "min_expected_edge_to_cost_ratio": 3.0,
                    "block_if_latest_bar_delay_gt_bars": 2,
                    "block_if_spread_too_wide": True,
                    "cooldown_after_losses_bars": 288,
                },
            }

        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
