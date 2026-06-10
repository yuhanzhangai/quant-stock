"""TSLA 因子自动迭代引擎。

核心逻辑：
1. 维护一个候选因子池（12+ 个因子）
2. 以「无因子纯动量」为 baseline
3. 每轮迭代测试一个新因子，跨 10+ 事件窗口验证
4. 因子正向 → 纳入策略，因子负向 → 丢弃
5. 支持 10x 杠杆（$50 本金 = $500 仓位）
6. 60 秒一轮，不断进化

用法：
    python scripts/tsla_factor_iterate.py          # 单次完整迭代
    python scripts/tsla_factor_iterate.py --loop    # 60 秒持续迭代
"""

import io
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import vectorbt as vbt
from loguru import logger

from src.backtest.costs import OKX_SWAP
from src.backtest.metrics import compute_metrics

# =========================================================================
# 杠杆配置
# =========================================================================
MARGIN_USD = 50.0  # 本金 $50
LEVERAGE = 10  # 10x 杠杆
POSITION_USD = MARGIN_USD * LEVERAGE  # $500 仓位
LIQUIDATION_PCT = 100.0 / LEVERAGE  # 10% 回撤 = 爆仓


# =========================================================================
# 事件目录（扩充到 17 个，确保 >= 10 个有效窗口）
# =========================================================================
@dataclass
class Event:
    date: str
    event_type: str
    title: str
    expected: str  # bullish / bearish / unknown


EVENTS: list[Event] = [
    Event("2026-04-22", "earnings", "Q1 2026 财报发布", "bearish"),
    Event("2026-02-26", "ceo", "Musk DOGE 争议 / 欧洲抵制", "bearish"),
    Event("2026-03-10", "ceo", "Musk 缩减 DOGE / 回归特斯拉", "bullish"),
    Event("2026-03-25", "ceo", "Musk 全职回归 CEO", "bullish"),
    Event("2026-03-01", "product", "Model Y Juniper 交付", "bullish"),
    Event("2026-03-15", "product", "FSD v13 推送", "bullish"),
    Event("2026-04-10", "product", "Robotaxi Austin 试运营", "bullish"),
    Event("2026-03-05", "regulatory", "对华关税生效", "bearish"),
    Event("2026-03-20", "regulatory", "EU 对华 EV 关税", "bullish"),
    Event("2026-04-02", "regulatory", "Trump 全面关税升级", "bearish"),
    Event("2026-04-09", "regulatory", "关税 90 天暂停", "bullish"),
    Event("2026-03-08", "macro", "美股大跌 / 衰退恐慌", "bearish"),
    Event("2026-03-18", "macro", "美联储鸽派表态", "bullish"),
    Event("2026-04-07", "macro", "全球暴跌 / 关税恐慌", "bearish"),
    Event("2026-04-14", "macro", "市场反弹 / 风险回升", "bullish"),
    Event("2026-03-03", "earnings", "2月交付数据：同比降", "bearish"),
    Event("2026-04-01", "earnings", "Q1 交付低于预期", "bearish"),
]


# =========================================================================
# 因子定义
# =========================================================================
def _safe_series(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col] if col in df.columns else df["close"]


def factor_rsi(df: pd.DataFrame, idx: int, period: int = 14) -> float:
    """RSI 过滤：RSI < 30 利好（超卖反弹），RSI > 70 利空（超买回落）。"""
    if idx < period:
        return 0.0
    close = df["close"].iloc[max(0, idx - period - 1) : idx + 1]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    if np.isnan(val):
        return 0.0
    if val < 30:
        return 1.0  # 超卖 → 利好
    elif val > 70:
        return -1.0  # 超买 → 利空
    return 0.0


def factor_macd(df: pd.DataFrame, idx: int) -> float:
    """MACD 柱方向确认。"""
    if idx < 26:
        return 0.0
    close = df["close"].iloc[: idx + 1]
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9).mean()
    hist = macd_line - signal_line
    val = hist.iloc[-1]
    if val > 0:
        return 1.0
    elif val < 0:
        return -1.0
    return 0.0


def factor_bollinger(df: pd.DataFrame, idx: int, period: int = 20) -> float:
    """布林带位置：价格在下轨附近利好，上轨附近利空。"""
    if idx < period:
        return 0.0
    close = df["close"].iloc[max(0, idx - period) : idx + 1]
    mid = close.rolling(period).mean().iloc[-1]
    std = close.rolling(period).std().iloc[-1]
    if np.isnan(mid) or np.isnan(std) or std == 0:
        return 0.0
    price = close.iloc[-1]
    z = (price - mid) / std
    if z < -1.5:
        return 1.0  # 下轨附近 → 超卖
    elif z > 1.5:
        return -1.0  # 上轨附近 → 超买
    return 0.0


def factor_volume_surge(df: pd.DataFrame, idx: int, lookback: int = 24) -> float:
    """成交量突增：事件时成交量 vs 过去 24h 平均。量能越大信号越可信。"""
    if idx < lookback:
        return 0.0
    vol = df["volume"].iloc[max(0, idx - lookback) : idx + 1]
    avg_vol = vol.iloc[:-1].mean()
    cur_vol = vol.iloc[-1]
    if avg_vol == 0:
        return 0.0
    ratio = cur_vol / avg_vol
    if ratio > 3.0:
        return 1.0  # 放量 3x+ → 信号可信
    elif ratio > 1.5:
        return 0.5
    return 0.0


def factor_pre_trend(df: pd.DataFrame, idx: int, days: int = 7) -> float:
    """事件前趋势：过去 N 天的动量方向。"""
    lookback = days * 24  # 1h K 线
    if idx < lookback:
        return 0.0
    price_now = df["close"].iloc[idx]
    price_before = df["close"].iloc[idx - lookback]
    change = (price_now - price_before) / price_before * 100
    if change > 3.0:
        return 1.0  # 上涨趋势
    elif change < -3.0:
        return -1.0  # 下跌趋势
    return 0.0


def factor_volatility_regime(df: pd.DataFrame, idx: int, period: int = 48) -> float:
    """波动率体制：高波动期更谨慎，低波动期更激进。"""
    if idx < period:
        return 0.0
    returns = df["close"].iloc[max(0, idx - period) : idx + 1].pct_change().dropna()
    if len(returns) < 2:
        return 0.0
    vol = returns.std() * np.sqrt(24 * 365) * 100  # 年化波动率
    if vol > 100:
        return -0.5  # 高波动 → 减仓信号
    elif vol < 30:
        return 0.5  # 低波动 → 加仓信号
    return 0.0


def factor_consecutive_bars(df: pd.DataFrame, idx: int, n: int = 3) -> float:
    """连续 K 线方向：连续 N 根同向 → 动量确认。"""
    if idx < n:
        return 0.0
    bars = df["close"].iloc[idx - n : idx + 1]
    changes = bars.diff().dropna()
    if (changes > 0).all():
        return 1.0  # 连续上涨
    elif (changes < 0).all():
        return -1.0  # 连续下跌
    return 0.0


def factor_range_position(df: pd.DataFrame, idx: int, period: int = 168) -> float:
    """价格在近 7 天高低区间的位置。"""
    if idx < period:
        return 0.0
    window = df["close"].iloc[max(0, idx - period) : idx + 1]
    high = window.max()
    low = window.min()
    if high == low:
        return 0.0
    pos = (df["close"].iloc[idx] - low) / (high - low)
    if pos < 0.2:
        return 1.0  # 接近区间底部 → 可能反弹
    elif pos > 0.8:
        return -1.0  # 接近区间顶部 → 可能回落
    return 0.0


def factor_candle_body(df: pd.DataFrame, idx: int) -> float:
    """K 线实体大小：大阳线/大阴线 = 强方向信号。"""
    o = df["open"].iloc[idx]
    c = df["close"].iloc[idx]
    h = df["high"].iloc[idx]
    l = df["low"].iloc[idx]
    body = abs(c - o)
    total = h - l
    if total == 0:
        return 0.0
    body_ratio = body / total
    if body_ratio > 0.7:
        return 1.0 if c > o else -1.0  # 大实体
    return 0.0


def factor_gap(df: pd.DataFrame, idx: int) -> float:
    """跳空缺口：开盘价 vs 前收的差距。"""
    if idx < 1:
        return 0.0
    prev_close = df["close"].iloc[idx - 1]
    cur_open = df["open"].iloc[idx]
    gap_pct = (cur_open - prev_close) / prev_close * 100
    if gap_pct > 0.5:
        return 1.0  # 高开
    elif gap_pct < -0.5:
        return -1.0  # 低开
    return 0.0


def factor_ema_cross(df: pd.DataFrame, idx: int) -> float:
    """EMA 8/21 交叉状态。"""
    if idx < 21:
        return 0.0
    close = df["close"].iloc[: idx + 1]
    ema8 = close.ewm(span=8).mean()
    ema21 = close.ewm(span=21).mean()
    if ema8.iloc[-1] > ema21.iloc[-1]:
        return 1.0  # 金叉
    else:
        return -1.0  # 死叉


def factor_atr_stop(df: pd.DataFrame, idx: int, period: int = 14) -> float:
    """ATR 动态止损参考：ATR 大 → 需要更宽止损 → 减仓。"""
    if idx < period:
        return 0.0
    h = df["high"].iloc[max(0, idx - period) : idx + 1]
    l = df["low"].iloc[max(0, idx - period) : idx + 1]
    c = df["close"].iloc[max(0, idx - period) : idx + 1]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    if np.isnan(atr):
        return 0.0
    atr_pct = atr / c.iloc[-1] * 100
    if atr_pct > 2.0:
        return -0.5  # 高 ATR → 谨慎
    elif atr_pct < 0.5:
        return 0.5  # 低 ATR → 可以激进
    return 0.0


# =========================================================================
# 因子池注册
# =========================================================================
FACTOR_POOL: dict[str, Callable] = {
    "rsi_14": factor_rsi,
    "macd_hist": factor_macd,
    "bollinger_20": factor_bollinger,
    "volume_surge": factor_volume_surge,
    "pre_trend_7d": factor_pre_trend,
    "volatility_regime": factor_volatility_regime,
    "consecutive_3bar": factor_consecutive_bars,
    "range_position_7d": factor_range_position,
    "candle_body": factor_candle_body,
    "gap_detect": factor_gap,
    "ema_8_21_cross": factor_ema_cross,
    "atr_dynamic_stop": factor_atr_stop,
}


# =========================================================================
# 回测引擎（含杠杆）
# =========================================================================
def run_leveraged_backtest(
    price: pd.Series,
    long_entries: pd.Series,
    long_exits: pd.Series,
    short_entries: pd.Series,
    short_exits: pd.Series,
    margin: float = MARGIN_USD,
    leverage: int = LEVERAGE,
) -> dict:
    """运行含杠杆的双向回测。

    Args:
        price: 收盘价
        long/short entries/exits: 信号
        margin: 保证金 ($50)
        leverage: 杠杆倍数 (10x)

    Returns:
        指标字典（含杠杆调整后的收益）
    """
    position_value = margin * leverage  # $500

    n_long = long_entries.sum()
    n_short = short_entries.sum()
    if n_long + n_short == 0:
        return {
            "total_trades": 0,
            "total_return_pct": 0,
            "sharpe_ratio": 0,
            "margin_return_pct": 0,
            "liquidated": False,
        }

    total_fee = OKX_SWAP.total_cost_per_trade

    # 用 position_value 做 init_cash 模拟杠杆仓位
    portfolio = vbt.Portfolio.from_signals(
        close=price,
        entries=long_entries,
        exits=long_exits,
        short_entries=short_entries,
        short_exits=short_exits,
        init_cash=position_value,
        fees=total_fee,
        freq="1h",
    )

    metrics = compute_metrics(portfolio)

    # 杠杆调整
    base_return = metrics["total_return"]
    margin_return = base_return * leverage  # 保证金收益率
    margin_pnl = margin * margin_return  # 实际盈亏

    # 爆仓检查
    max_dd = metrics["max_drawdown_pct"] / 100
    liquidated = max_dd * leverage >= 1.0  # 回撤 * 杠杆 >= 100%

    metrics["leverage"] = leverage
    metrics["margin_usd"] = margin
    metrics["position_usd"] = position_value
    metrics["margin_return_pct"] = margin_return * 100
    metrics["margin_pnl_usd"] = margin_pnl
    metrics["leveraged_max_dd_pct"] = max_dd * leverage * 100
    metrics["liquidated"] = liquidated
    metrics["n_long"] = int(n_long)
    metrics["n_short"] = int(n_short)

    return metrics


# =========================================================================
# 策略信号生成（含因子过滤）
# =========================================================================
def generate_signals_with_factors(
    df: pd.DataFrame,
    active_factors: list[str],
    reaction_hours: int = 4,
    hold_hours: int = 96,
    momentum_threshold: float = 0.3,
    stop_pct: float = 2.0,
    take_profit_pct: float = 8.0,
    factor_threshold: float = 0.0,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, list[dict]]:
    """生成带因子过滤的交易信号。

    Args:
        df: OHLCV DataFrame
        active_factors: 激活的因子名列表
        factor_threshold: 因子总分阈值，>0 确认做多，<0 确认做空

    Returns:
        (long_entries, long_exits, short_entries, short_exits, trade_log)
    """
    price = df["close"]
    long_entries = pd.Series(False, index=price.index)
    long_exits = pd.Series(False, index=price.index)
    short_entries = pd.Series(False, index=price.index)
    short_exits = pd.Series(False, index=price.index)
    trade_log: list[dict] = []

    price_idx = price.index

    for event in EVENTS:
        event_ts = pd.Timestamp(event.date, tz="UTC")
        mask = price_idx >= event_ts
        if not mask.any():
            continue

        first_bar = mask.argmax()
        reaction_end = min(first_bar + reaction_hours, len(price) - 1)
        if reaction_end <= first_bar:
            continue

        entry_price = price.iloc[first_bar]
        reaction_price = price.iloc[reaction_end]
        change_pct = (reaction_price - entry_price) / entry_price * 100

        if abs(change_pct) < momentum_threshold:
            continue

        # 计算因子得分
        factor_scores: dict[str, float] = {}
        for fname in active_factors:
            func = FACTOR_POOL.get(fname)
            if func:
                score = func(df, reaction_end)
                factor_scores[fname] = score

        total_factor_score = sum(factor_scores.values()) if factor_scores else 0.0

        # 方向判断: 动量方向 + 因子确认
        momentum_direction = 1 if change_pct > 0 else -1

        if active_factors:
            # 因子和动量方向一致 → 入场，不一致 → 跳过
            factor_direction = (
                1 if total_factor_score > factor_threshold else (-1 if total_factor_score < -factor_threshold else 0)
            )

            if factor_direction == 0:
                # 因子中性，跟随动量
                is_long = momentum_direction > 0
            elif factor_direction == momentum_direction:
                # 因子确认动量 → 强信号
                is_long = momentum_direction > 0
            else:
                # 因子和动量矛盾 → 跳过
                continue
        else:
            is_long = momentum_direction > 0

        entry_bar = reaction_end
        if is_long:
            long_entries.iloc[entry_bar] = True
        else:
            short_entries.iloc[entry_bar] = True

        entry_val = price.iloc[entry_bar]
        exit_reason = "到期"
        exit_pnl = 0.0

        for j in range(entry_bar + 1, min(entry_bar + hold_hours, len(price))):
            cur = price.iloc[j]
            pnl = ((cur - entry_val) / entry_val * 100) if is_long else ((entry_val - cur) / entry_val * 100)

            # 杠杆爆仓检查
            if pnl * LEVERAGE <= -100:
                if is_long:
                    long_exits.iloc[j] = True
                else:
                    short_exits.iloc[j] = True
                exit_reason = "爆仓"
                exit_pnl = -100.0 / LEVERAGE
                break

            if pnl >= take_profit_pct:
                if is_long:
                    long_exits.iloc[j] = True
                else:
                    short_exits.iloc[j] = True
                exit_reason = "止盈"
                exit_pnl = pnl
                break
            elif pnl <= -stop_pct:
                if is_long:
                    long_exits.iloc[j] = True
                else:
                    short_exits.iloc[j] = True
                exit_reason = "止损"
                exit_pnl = pnl
                break
        else:
            force = min(entry_bar + hold_hours, len(price) - 1)
            if is_long:
                long_exits.iloc[force] = True
                exit_pnl = (price.iloc[force] - entry_val) / entry_val * 100
            else:
                short_exits.iloc[force] = True
                exit_pnl = (entry_val - price.iloc[force]) / entry_val * 100

        trade_log.append(
            {
                "date": event.date,
                "type": event.event_type,
                "title": event.title[:25],
                "direction": "LONG" if is_long else "SHORT",
                "entry": round(entry_val, 2),
                "pnl_pct": round(exit_pnl, 2),
                "pnl_leveraged": round(exit_pnl * LEVERAGE, 2),
                "exit_reason": exit_reason,
                "factor_score": round(total_factor_score, 2),
                "factors": factor_scores,
            }
        )

    return long_entries, long_exits, short_entries, short_exits, trade_log


# =========================================================================
# 迭代引擎
# =========================================================================
@dataclass
class IterationResult:
    """单轮迭代结果。"""

    factor_name: str
    baseline_sharpe: float
    new_sharpe: float
    baseline_return: float
    new_return: float
    improvement: float  # sharpe 变化
    n_trades_baseline: int
    n_trades_new: int
    events_tested: int
    verdict: str  # POSITIVE / NEGATIVE / NEUTRAL
    details: dict = field(default_factory=dict)


class FactorIterator:
    """因子自动迭代器。"""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.active_factors: list[str] = []
        self.tested_factors: dict[str, str] = {}  # name → verdict
        self.untested_factors: list[str] = list(FACTOR_POOL.keys())
        self.iteration = 0
        self.history: list[IterationResult] = []
        self.state_path = Path("reports/tsla/factor_state.json")

        # 尝试加载之前的状态
        self._load_state()

    def _load_state(self) -> None:
        """加载上次迭代状态。"""
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    state = json.load(f)
                self.active_factors = state.get("active_factors", [])
                self.tested_factors = state.get("tested_factors", {})
                self.iteration = state.get("iteration", 0)
                # 重建 untested 列表
                self.untested_factors = [f for f in FACTOR_POOL if f not in self.tested_factors]
                logger.info(
                    f"加载状态 | 迭代 #{self.iteration} | "
                    f"活跃因子: {self.active_factors} | "
                    f"待测: {len(self.untested_factors)}"
                )
            except (json.JSONDecodeError, KeyError):
                pass

    def _save_state(self) -> None:
        """保存迭代状态 + 追加迭代日志。"""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "active_factors": self.active_factors,
            "tested_factors": self.tested_factors,
            "iteration": self.iteration,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
        with open(self.state_path, "w") as f:
            json.dump(state, f, indent=2)

    def _append_iteration_log(self, result: "IterationResult") -> None:
        """追加迭代记录到 CSV（持久化，方便后期检查）。"""
        log_path = self.state_path.parent / "iteration_log.csv"
        row = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "iteration": self.iteration,
            "factor": result.factor_name,
            "verdict": result.verdict,
            "baseline_sharpe": round(result.baseline_sharpe, 4),
            "new_sharpe": round(result.new_sharpe, 4),
            "improvement": round(result.improvement, 4),
            "baseline_return_pct": round(result.baseline_return, 2),
            "new_return_pct": round(result.new_return, 2),
            "trades_baseline": result.n_trades_baseline,
            "trades_new": result.n_trades_new,
            "events_tested": result.events_tested,
            "active_factors": "|".join(self.active_factors) if self.active_factors else "(none)",
            "liquidated": result.details.get("liquidated", False),
            "leveraged_dd_pct": round(result.details.get("leveraged_dd", 0), 2),
            "win_rate": round(result.details.get("win_rate", 0), 1),
            "data_rows": len(self.df),
        }
        write_header = not log_path.exists()
        with open(log_path, "a", encoding="utf-8") as f:
            if write_header:
                f.write(",".join(row.keys()) + "\n")
            f.write(",".join(str(v) for v in row.values()) + "\n")

    def _append_cycle_snapshot(self, cycle: int, price: float, news: dict, baseline: dict) -> None:
        """追加每轮循环快照到 CSV。"""
        snap_path = self.state_path.parent / "cycle_snapshots.csv"
        row = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "cycle": cycle,
            "price": round(price, 2),
            "news_sentiment": news.get("overall", "unknown"),
            "news_score": round(news.get("avg_score", 0), 4),
            "news_bull": news.get("bullish_count", 0),
            "news_bear": news.get("bearish_count", 0),
            "margin_return_pct": round(baseline.get("margin_return_pct", 0), 2),
            "margin_pnl_usd": round(baseline.get("margin_pnl_usd", 0), 2),
            "sharpe": round(baseline.get("sharpe_ratio", 0), 4),
            "win_rate": round(baseline.get("win_rate_pct", 0), 1),
            "total_trades": baseline.get("total_trades", 0),
            "leveraged_dd_pct": round(baseline.get("leveraged_max_dd_pct", 0), 2),
            "active_factors": "|".join(self.active_factors) if self.active_factors else "(none)",
            "data_rows": len(self.df),
        }
        write_header = not snap_path.exists()
        with open(snap_path, "a", encoding="utf-8") as f:
            if write_header:
                f.write(",".join(row.keys()) + "\n")
            f.write(",".join(str(v) for v in row.values()) + "\n")

    def run_baseline(self) -> dict:
        """运行当前活跃因子组合的 baseline。"""
        le, lx, se, sx, log = generate_signals_with_factors(self.df, self.active_factors)
        metrics = run_leveraged_backtest(self.df["close"], le, lx, se, sx)
        metrics["trade_log"] = log
        return metrics

    def test_factor(self, factor_name: str) -> IterationResult:
        """测试单个因子的增量贡献。

        Args:
            factor_name: 因子名

        Returns:
            IterationResult
        """
        self.iteration += 1

        # 1. Baseline（当前活跃因子）
        baseline = self.run_baseline()
        baseline_sharpe = baseline.get("sharpe_ratio", 0)
        baseline_return = baseline.get("margin_return_pct", 0)
        baseline_trades = baseline.get("total_trades", 0)

        # 2. 加入新因子
        test_factors = self.active_factors + [factor_name]
        le, lx, se, sx, log = generate_signals_with_factors(self.df, test_factors)
        new_metrics = run_leveraged_backtest(self.df["close"], le, lx, se, sx)
        new_sharpe = new_metrics.get("sharpe_ratio", 0)
        new_return = new_metrics.get("margin_return_pct", 0)
        new_trades = new_metrics.get("total_trades", 0)

        # 3. 判定
        improvement = new_sharpe - baseline_sharpe
        events_with_data = len([e for e in EVENTS if pd.Timestamp(e.date, tz="UTC") >= self.df.index[0]])

        if new_trades == 0:
            verdict = "NEGATIVE"  # 过滤掉了所有交易
        elif improvement > 0.1 and new_return > baseline_return:
            verdict = "POSITIVE"
        elif improvement < -0.1 or new_return < baseline_return - 5:
            verdict = "NEGATIVE"
        else:
            verdict = "NEUTRAL"

        result = IterationResult(
            factor_name=factor_name,
            baseline_sharpe=baseline_sharpe,
            new_sharpe=new_sharpe,
            baseline_return=baseline_return,
            new_return=new_return,
            improvement=improvement,
            n_trades_baseline=baseline_trades,
            n_trades_new=new_trades,
            events_tested=events_with_data,
            verdict=verdict,
            details={
                "margin_pnl": new_metrics.get("margin_pnl_usd", 0),
                "leveraged_dd": new_metrics.get("leveraged_max_dd_pct", 0),
                "liquidated": new_metrics.get("liquidated", False),
                "win_rate": new_metrics.get("win_rate_pct", 0),
                "trade_log": log,
            },
        )

        # 4. 纳入或丢弃
        if verdict == "POSITIVE":
            self.active_factors.append(factor_name)

        self.tested_factors[factor_name] = verdict
        if factor_name in self.untested_factors:
            self.untested_factors.remove(factor_name)

        self.history.append(result)
        self._save_state()
        self._append_iteration_log(result)

        return result

    def iterate_once(self) -> IterationResult | None:
        """执行一轮迭代。"""
        if not self.untested_factors:
            # 所有因子都测过了，尝试组合优化
            logger.info("所有因子已测试完毕，进入组合优化阶段")
            return self._optimize_combinations()

        factor = self.untested_factors[0]
        return self.test_factor(factor)

    def _optimize_combinations(self) -> IterationResult | None:
        """测试移除已纳入因子是否能改善。"""
        if len(self.active_factors) <= 1:
            return None

        worst_factor = None
        worst_improvement = 0

        for f in self.active_factors:
            test_factors = [x for x in self.active_factors if x != f]
            le, lx, se, sx, _ = generate_signals_with_factors(self.df, test_factors)
            metrics = run_leveraged_backtest(self.df["close"], le, lx, se, sx)

            baseline = self.run_baseline()
            improvement = metrics.get("sharpe_ratio", 0) - baseline.get("sharpe_ratio", 0)

            if improvement > worst_improvement:
                worst_improvement = improvement
                worst_factor = f

        if worst_factor and worst_improvement > 0.1:
            self.active_factors.remove(worst_factor)
            self.tested_factors[worst_factor] = "REMOVED"
            self._save_state()
            logger.info(f"移除因子 {worst_factor}，夏普提升 {worst_improvement:+.3f}")
            return IterationResult(
                factor_name=f"-{worst_factor}",
                baseline_sharpe=0,
                new_sharpe=0,
                baseline_return=0,
                new_return=0,
                improvement=worst_improvement,
                n_trades_baseline=0,
                n_trades_new=0,
                events_tested=0,
                verdict="REMOVED",
            )
        return None

    def print_status(self) -> None:
        """打印当前状态。"""
        print(f"\n{'=' * 90}")
        print(f"TSLA 因子迭代引擎 | 迭代 #{self.iteration} | {datetime.now(tz=UTC).strftime('%H:%M:%S')} UTC")
        print(f"{'=' * 90}")
        print(f"  杠杆: {LEVERAGE}x | 本金: ${MARGIN_USD} | 仓位: ${POSITION_USD}")
        print(f"  爆仓线: 价格反向移动 {LIQUIDATION_PCT:.1f}%")
        print(f"\n  活跃因子 ({len(self.active_factors)}):")
        for f in self.active_factors:
            print(f"    [+] {f}")
        print(f"\n  待测因子 ({len(self.untested_factors)}):")
        for f in self.untested_factors[:5]:
            print(f"    [ ] {f}")
        if len(self.untested_factors) > 5:
            print(f"    ... 还有 {len(self.untested_factors) - 5} 个")

        # 当前策略表现
        baseline = self.run_baseline()
        print("\n  当前策略表现:")
        print(f"    交易次数: {baseline.get('total_trades', 0)}")
        print(f"    保证金收益: {baseline.get('margin_return_pct', 0):+.2f}%")
        print(f"    保证金盈亏: ${baseline.get('margin_pnl_usd', 0):+.2f}")
        print(f"    夏普比: {baseline.get('sharpe_ratio', 0):.3f}")
        print(f"    杠杆回撤: {baseline.get('leveraged_max_dd_pct', 0):.2f}%")
        print(f"    爆仓: {'是' if baseline.get('liquidated') else '否'}")

        # 交易日志
        log = baseline.get("trade_log", [])
        if log:
            print("\n  交易日志:")
            for t in log:
                icon = "+" if t["pnl_leveraged"] > 0 else "-"
                print(
                    f"    [{icon}] {t['date']} {t['direction']:>5} | "
                    f"{t['title']:<25} | "
                    f"P&L {t['pnl_pct']:+.2f}% (x{LEVERAGE}={t['pnl_leveraged']:+.2f}%) | "
                    f"{t['exit_reason']}"
                )

    def print_iteration_result(self, result: IterationResult) -> None:
        """打印单轮迭代结果。"""
        verdict_icon = {"POSITIVE": "[++]", "NEGATIVE": "[--]", "NEUTRAL": "[==]", "REMOVED": "[XX]"}
        icon = verdict_icon.get(result.verdict, "[??]")

        print(f"\n{'─' * 90}")
        print(f"  {icon} 因子: {result.factor_name}")
        print(f"  事件覆盖: {result.events_tested} 个")
        print(
            f"  Baseline → 新 夏普: {result.baseline_sharpe:.3f} → {result.new_sharpe:.3f} ({result.improvement:+.3f})"
        )
        print(f"  Baseline → 新 收益: {result.baseline_return:+.2f}% → {result.new_return:+.2f}%")
        print(f"  交易数: {result.n_trades_baseline} → {result.n_trades_new}")
        print(f"  判定: {result.verdict}")

        if result.details.get("liquidated"):
            print(f"  !! 触发爆仓！杠杆回撤: {result.details.get('leveraged_dd', 0):.2f}%")

        # 每笔交易的因子评分
        log = result.details.get("trade_log", [])
        if log:
            print("\n  逐事件因子评分:")
            for t in log:
                factors_str = " | ".join(f"{k}={v:+.1f}" for k, v in t.get("factors", {}).items())
                print(f"    {t['date']} {t['direction']:>5} | 总分={t['factor_score']:+.2f} | {factors_str}")

    def print_final_report(self) -> None:
        """打印最终报告。"""
        print(f"\n{'=' * 90}")
        print(f"最终因子筛选报告 | 共 {self.iteration} 轮迭代")
        print(f"{'=' * 90}")

        print("\n  因子筛选结果:")
        for fname, verdict in self.tested_factors.items():
            icon = {"POSITIVE": "[++]", "NEGATIVE": "[--]", "NEUTRAL": "[==]", "REMOVED": "[XX]"}
            print(f"    {icon.get(verdict, '[??]')} {fname}: {verdict}")

        print(f"\n  最终活跃因子: {self.active_factors if self.active_factors else '(无，纯动量策略)'}")

        # 最终表现
        final = self.run_baseline()
        print(f"\n  最终策略表现 ({LEVERAGE}x 杠杆 / ${MARGIN_USD} 本金):")
        print(f"    交易次数: {final.get('total_trades', 0)}")
        print(f"    保证金收益率: {final.get('margin_return_pct', 0):+.2f}%")
        print(f"    保证金盈亏: ${final.get('margin_pnl_usd', 0):+.2f}")
        print(f"    夏普比: {final.get('sharpe_ratio', 0):.3f}")
        print(f"    胜率: {final.get('win_rate_pct', 0):.1f}%")
        print(f"    杠杆最大回撤: {final.get('leveraged_max_dd_pct', 0):.2f}%")
        print(f"    爆仓: {'是' if final.get('liquidated') else '否'}")


# =========================================================================
# 随机对照组（Placebo Test）
# =========================================================================
def run_placebo_test(
    df: pd.DataFrame,
    n_random: int = 20,
    hold_hours: int = 96,
    reaction_hours: int = 4,
    momentum_threshold: float = 0.3,
) -> dict:
    """随机入场 vs 新闻入场对比，排除策略幻觉。

    在非新闻时间随机选取 N 个入场点，用相同策略逻辑交易，
    对比新闻入场和随机入场的收益差异。

    Args:
        df: OHLCV DataFrame
        n_random: 随机入场次数
        hold_hours: 持仓小时数
        reaction_hours: 反应观察期
        momentum_threshold: 动量阈值

    Returns:
        对照组结果字典
    """
    price = df["close"]

    # ---- 新闻事件入场的收益 ----
    news_returns: list[float] = []
    news_event_indices: set[int] = set()

    for event in EVENTS:
        event_ts = pd.Timestamp(event.date, tz="UTC")
        mask = df.index >= event_ts
        if not mask.any():
            continue
        first_bar = mask.argmax()
        reaction_end = min(first_bar + reaction_hours, len(price) - 1)
        if reaction_end <= first_bar or reaction_end + hold_hours >= len(price):
            continue

        entry_p = price.iloc[reaction_end]
        # 跟随动量方向
        change = (price.iloc[reaction_end] - price.iloc[first_bar]) / price.iloc[first_bar] * 100
        if abs(change) < momentum_threshold:
            continue

        exit_idx = min(reaction_end + hold_hours, len(price) - 1)
        exit_p = price.iloc[exit_idx]

        if change > 0:  # 做多
            ret = (exit_p - entry_p) / entry_p * 100
        else:  # 做空
            ret = (entry_p - exit_p) / entry_p * 100

        news_returns.append(ret)
        # 标记新闻窗口，避免随机入场落在新闻区间
        for i in range(max(0, first_bar - 24), min(len(price), reaction_end + hold_hours + 24)):
            news_event_indices.add(i)

    # ---- 随机入场的收益（排除新闻窗口）----
    random_returns: list[float] = []
    # 可用入场点：去掉新闻窗口、去掉前后边界
    safe_range = [i for i in range(reaction_hours + 50, len(price) - hold_hours - 10) if i not in news_event_indices]

    if safe_range and len(safe_range) >= n_random:
        rng = np.random.default_rng(seed=42 + len(df))  # seed 随数据量变化
        chosen = rng.choice(safe_range, size=min(n_random, len(safe_range)), replace=False)

        for idx in chosen:
            entry_p = price.iloc[idx]
            # 同样用前 reaction_hours 的动量方向
            pre_p = price.iloc[idx - reaction_hours]
            change = (entry_p - pre_p) / pre_p * 100

            exit_idx = min(idx + hold_hours, len(price) - 1)
            exit_p = price.iloc[exit_idx]

            if abs(change) >= momentum_threshold:
                if change > 0:
                    ret = (exit_p - entry_p) / entry_p * 100
                else:
                    ret = (entry_p - exit_p) / entry_p * 100
                random_returns.append(ret)
            else:
                # 动量不够也记录（做多 by default）
                ret = (exit_p - entry_p) / entry_p * 100
                random_returns.append(ret)

    news_avg = np.mean(news_returns) if news_returns else 0.0
    rand_avg = np.mean(random_returns) if random_returns else 0.0
    alpha = news_avg - rand_avg

    news_wr = (sum(1 for r in news_returns if r > 0) / len(news_returns) * 100) if news_returns else 0
    rand_wr = (sum(1 for r in random_returns if r > 0) / len(random_returns) * 100) if random_returns else 0

    # 简单显著性判断
    if len(news_returns) < 3 or len(random_returns) < 3:
        sig = "样本不足"
    elif alpha > 2.0 and news_wr > rand_wr + 10:
        sig = "显著 (新闻有真Alpha)"
    elif alpha > 0.5:
        sig = "弱显著 (可能有Alpha)"
    elif alpha > -0.5:
        sig = "不显著 (新闻无明显优势)"
    else:
        sig = "反显著 (随机比新闻好!)"

    return {
        "news_avg_return": news_avg,
        "random_avg_return": rand_avg,
        "alpha": alpha,
        "significance": sig,
        "news_win_rate": news_wr,
        "random_win_rate": rand_wr,
        "n_news": len(news_returns),
        "n_random": len(random_returns),
        "news_returns": news_returns,
        "random_returns": random_returns,
    }


# =========================================================================
# 实时数据刷新
# =========================================================================
def refresh_data() -> pd.DataFrame:
    """从 OKX 拉取最新 K 线，合并到本地 Parquet。

    Returns:
        最新完整 DataFrame
    """
    import asyncio

    import ccxt.async_support as ccxt_async

    data_path = Path("data/parquet/ohlcv/swap/TSLA-USDT/1h.parquet")

    # 加载已有数据
    if data_path.exists():
        df_old = pd.read_parquet(data_path)
        if not isinstance(df_old.index, pd.DatetimeIndex):
            if "timestamp" in df_old.columns:
                df_old = df_old.set_index("timestamp")
            df_old.index = pd.to_datetime(df_old.index, utc=True)
        last_ts = int(df_old.index[-1].timestamp() * 1000) + 1
    else:
        df_old = pd.DataFrame()
        last_ts = 1772010000000  # TSLA 合约创建时间

    async def _fetch() -> list:
        ex = ccxt_async.okx({"enableRateLimit": True})
        try:
            data = await ex.fetch_ohlcv("TSLA/USDT:USDT", "1h", since=last_ts, limit=100)
            return data
        finally:
            await ex.close()

    try:
        new_candles = asyncio.run(_fetch())
    except Exception as e:
        logger.warning(f"数据刷新失败: {e}")
        return df_old if not df_old.empty else pd.DataFrame()

    if not new_candles:
        return df_old

    df_new = pd.DataFrame(new_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df_new["timestamp"] = pd.to_datetime(df_new["timestamp"], unit="ms", utc=True)
    df_new = df_new.set_index("timestamp")

    if not df_old.empty:
        df = pd.concat([df_old, df_new])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    else:
        df = df_new

    # 保存
    data_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(data_path)

    n_new = len(df) - len(df_old)
    if n_new > 0:
        logger.info(f"数据刷新 | +{n_new} 根新 K 线 | 总计 {len(df)} | 最新: {df.index[-1]}")
    return df


# =========================================================================
# 新闻检查
# =========================================================================
def check_news() -> dict:
    """检查最新 TSLA 新闻情绪。

    Returns:
        情绪摘要字典
    """
    try:
        from src.news.tsla_news_fetcher import (
            fetch_google_news_rss,
            get_sentiment_summary,
            save_news_cache,
        )

        news = fetch_google_news_rss("Tesla TSLA", max_items=15)
        if news:
            save_news_cache(news)
            summary = get_sentiment_summary(news)
            return summary
    except Exception as e:
        logger.debug(f"新闻检查跳过: {e}")
    return {"overall": "unknown", "avg_score": 0, "count": 0, "bullish_count": 0, "bearish_count": 0}


# =========================================================================
# 呼吸循环模式
# =========================================================================
def breathing_loop(interval: int = 60) -> None:
    """60 秒呼吸循环。

    每轮做以下事情：
    1. 刷新最新 K 线数据
    2. 检查最新新闻情绪
    3. 重新计算当前策略信号
    4. 测试下一个未验证因子（或重新验证旧因子）
    5. 输出实时状态仪表板
    """
    cycle = 0
    best_sharpe_ever = 0.0
    factor_retest_queue: list[str] = []  # 重测队列

    logger.info(f"启动呼吸循环 | 间隔 {interval}s | Ctrl+C 退出")

    while True:
        cycle += 1
        cycle_start = time.time()
        now_utc = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n{'=' * 90}")
        print(f"  TSLA 呼吸循环 #{cycle} | {now_utc} UTC")
        print(f"{'=' * 90}")

        # ---- Step 1: 刷新数据 ----
        print("\n  [1/5] 刷新K线数据...")
        df = refresh_data()
        if df.empty:
            print("    数据为空，等待下一轮")
            time.sleep(interval)
            continue

        latest_price = df["close"].iloc[-1]
        latest_time = df.index[-1]
        price_1h_ago = df["close"].iloc[-2] if len(df) > 1 else latest_price
        change_1h = (latest_price - price_1h_ago) / price_1h_ago * 100

        print(f"    最新: ${latest_price:.2f} ({change_1h:+.2f}% 1h) | {latest_time}")
        print(f"    K线数: {len(df)} | 范围: {df.index[0].strftime('%m-%d')} ~ {df.index[-1].strftime('%m-%d %H:%M')}")

        # ---- Step 2: 检查新闻 ----
        print("\n  [2/5] 检查新闻情绪...")
        news_summary = check_news()
        news_icon = {"bullish": "[+]", "bearish": "[-]", "neutral": "[=]"}.get(
            news_summary.get("overall", "unknown"), "[?]"
        )
        print(
            f"    {news_icon} {news_summary.get('overall', 'unknown')} | "
            f"score: {news_summary.get('avg_score', 0):+.3f} | "
            f"bull/bear: {news_summary.get('bullish_count', 0)}/{news_summary.get('bearish_count', 0)}"
        )

        # ---- Step 3: 重建迭代器（用最新数据）----
        iterator = FactorIterator(df)

        # ---- Step 4: 策略状态 ----
        print("\n  [3/5] 当前策略表现...")
        baseline = iterator.run_baseline()
        margin_ret = baseline.get("margin_return_pct", 0)
        margin_pnl = baseline.get("margin_pnl_usd", 0)
        sharpe = baseline.get("sharpe_ratio", 0)
        lev_dd = baseline.get("leveraged_max_dd_pct", 0)
        trades = baseline.get("total_trades", 0)
        win_rate = baseline.get("win_rate_pct", 0)

        if sharpe > best_sharpe_ever:
            best_sharpe_ever = sharpe

        print(f"    本金: ${MARGIN_USD} x {LEVERAGE}x = ${POSITION_USD} 仓位")
        print(f"    保证金收益: {margin_ret:+.2f}% (${margin_pnl:+.2f})")
        print(f"    夏普: {sharpe:.3f} (历史最佳: {best_sharpe_ever:.3f})")
        print(f"    胜率: {win_rate:.0f}% | 交易: {trades} 笔 | 杠杆回撤: {lev_dd:.1f}%")

        # 记录快照
        iterator._append_cycle_snapshot(cycle, latest_price, news_summary, baseline)

        # 交易日志
        trade_log = baseline.get("trade_log", [])
        if trade_log:
            print("\n    交易记录:")
            for t in trade_log:
                icon = "W" if t["pnl_leveraged"] > 0 else "L"
                print(
                    f"      [{icon}] {t['date']} {t['direction']:>5} "
                    f"${t['entry']:.2f} → {t['pnl_pct']:+.2f}% "
                    f"(x{LEVERAGE}={t['pnl_leveraged']:+.1f}%) {t['exit_reason']}"
                )

        # ---- Step 5: 因子迭代 ----
        print("\n  [4/5] 因子迭代...")
        print(f"    活跃因子: {iterator.active_factors if iterator.active_factors else '(纯动量)'}")

        # 检查是否有未测试的因子
        if iterator.untested_factors:
            factor = iterator.untested_factors[0]
            print(f"    测试新因子: {factor}")
            result = iterator.test_factor(factor)
            v_icon = {"POSITIVE": "++", "NEGATIVE": "--", "NEUTRAL": "=="}.get(result.verdict, "??")
            print(
                f"    [{v_icon}] {factor}: "
                f"夏普 {result.baseline_sharpe:.3f} → {result.new_sharpe:.3f} "
                f"({result.improvement:+.3f}) | {result.verdict}"
            )
        else:
            # 所有因子已测，用新数据重新验证一个旧因子
            if not factor_retest_queue:
                factor_retest_queue = list(FACTOR_POOL.keys())
            factor = factor_retest_queue.pop(0)
            factor_retest_queue.append(factor)  # 循环

            # 重置这个因子，重新测试
            old_verdict = iterator.tested_factors.get(factor, "N/A")
            if factor in iterator.tested_factors:
                del iterator.tested_factors[factor]
            if factor in iterator.active_factors:
                iterator.active_factors.remove(factor)
            iterator.untested_factors.append(factor)

            result = iterator.test_factor(factor)
            v_icon = {"POSITIVE": "++", "NEGATIVE": "--", "NEUTRAL": "=="}.get(result.verdict, "??")
            changed = " *CHANGED*" if result.verdict != old_verdict else ""
            print(
                f"    重测 [{v_icon}] {factor}: "
                f"夏普 {result.baseline_sharpe:.3f} → {result.new_sharpe:.3f} "
                f"({result.improvement:+.3f}) | {old_verdict} → {result.verdict}{changed}"
            )

        # ---- Step 6: 随机对照组（排除幻觉）----
        # 每 6 轮做一次完整对照测试（避免每轮都跑太慢）
        if cycle % 6 == 1:
            print("\n  [5/7] 随机对照组检测（排除幻觉）...")
            placebo = run_placebo_test(df, n_random=20)
            news_avg_ret = placebo["news_avg_return"]
            rand_avg_ret = placebo["random_avg_return"]
            alpha = placebo["alpha"]
            p_val_hint = placebo["significance"]

            print(f"    新闻事件入场 平均收益: {news_avg_ret:+.2f}%")
            print(f"    随机时间入场 平均收益: {rand_avg_ret:+.2f}%")
            print(f"    Alpha(新闻-随机): {alpha:+.2f}%")
            print(f"    显著性: {p_val_hint}")

            if alpha <= 0:
                print("    !! 警告: 新闻因子无超额收益，策略可能是幻觉！")

            # 记录到 CSV
            placebo_path = Path("reports/tsla/placebo_log.csv")
            p_row = {
                "timestamp": datetime.now(tz=UTC).isoformat(),
                "cycle": cycle,
                "news_avg_return": round(news_avg_ret, 4),
                "random_avg_return": round(rand_avg_ret, 4),
                "alpha": round(alpha, 4),
                "significance": p_val_hint,
                "news_win_rate": round(placebo["news_win_rate"], 2),
                "random_win_rate": round(placebo["random_win_rate"], 2),
                "n_news": placebo["n_news"],
                "n_random": placebo["n_random"],
                "data_rows": len(df),
            }
            write_header = not placebo_path.exists()
            with open(placebo_path, "a", encoding="utf-8") as f:
                if write_header:
                    f.write(",".join(p_row.keys()) + "\n")
                f.write(",".join(str(v) for v in p_row.values()) + "\n")
        else:
            print(f"\n  [5/7] 对照组检测（每6轮一次，下次: #{(cycle // 6 + 1) * 6 + 1}）")

        # ---- Step 7: 实时信号 ----
        print("\n  [6/7] 实时信号检查...")

        # 检查最近是否有事件触发
        now_ts = pd.Timestamp.now(tz="UTC")
        recent_events = [
            e for e in EVENTS if abs((now_ts - pd.Timestamp(e.date, tz="UTC")).total_seconds()) < 7 * 24 * 3600
        ]

        if recent_events:
            for e in recent_events:
                event_ts = pd.Timestamp(e.date, tz="UTC")
                hours_since = (now_ts - event_ts).total_seconds() / 3600

                # 如果在反应窗口内
                if 0 < hours_since < 96:
                    first_bar = (df.index >= event_ts).argmax()
                    if first_bar > 0 and first_bar + 4 < len(df):
                        entry_p = df["close"].iloc[first_bar]
                        current_p = df["close"].iloc[-1]
                        cur_change = (current_p - entry_p) / entry_p * 100
                        direction = "LONG" if cur_change > 0 else "SHORT"
                        lev_pnl = cur_change * LEVERAGE

                        status = "持仓中" if hours_since < 96 else "已到期"
                        print(
                            f"    >> {e.title} ({hours_since:.0f}h前) | "
                            f"{direction} ${entry_p:.2f} → ${current_p:.2f} | "
                            f"浮动 {cur_change:+.2f}% (x{LEVERAGE}={lev_pnl:+.1f}%) | {status}"
                        )
                elif -24 < hours_since <= 0:
                    print(f"    >> 即将到来: {e.title} ({-hours_since:.0f}h后)")
        else:
            print("    无近期事件触发")

        # 新闻情绪预警
        avg_score = news_summary.get("avg_score", 0)
        if abs(avg_score) > 0.3:
            sentiment = "强烈利好" if avg_score > 0.3 else "强烈利空"
            print(f"\n  [7/7] !! 新闻情绪预警: {sentiment} (score={avg_score:+.3f})")

        # ---- 循环等待 ----
        elapsed = time.time() - cycle_start
        wait = max(1, interval - elapsed)
        print(f"\n  耗时 {elapsed:.1f}s | 下一轮 {wait:.0f}s 后")
        print(f"{'─' * 90}")

        time.sleep(wait)


# =========================================================================
# 主函数
# =========================================================================
def main() -> None:
    """主入口。"""
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        try:
            breathing_loop(interval=60)
        except KeyboardInterrupt:
            print("\n\n用户中断，打印最终报告...")
            df = pd.read_parquet("data/parquet/ohlcv/swap/TSLA-USDT/1h.parquet")
            if not isinstance(df.index, pd.DatetimeIndex):
                if "timestamp" in df.columns:
                    df = df.set_index("timestamp")
                df.index = pd.to_datetime(df.index, utc=True)
            iterator = FactorIterator(df)
            iterator.print_final_report()
        return

    # 单次完整迭代模式
    data_path = Path("data/parquet/ohlcv/swap/TSLA-USDT/1h.parquet")
    if not data_path.exists():
        logger.error(f"数据不存在: {data_path}，请先运行 tsla_fetch_data.py")
        sys.exit(1)

    df = pd.read_parquet(data_path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True)

    logger.info(f"数据加载 | {len(df)} 根 K 线 | {df.index[0]} ~ {df.index[-1]}")

    iterator = FactorIterator(df)
    iterator.print_status()

    logger.info(f"单次模式：测试全部 {len(FACTOR_POOL)} 个因子")
    for i, fname in enumerate(list(FACTOR_POOL.keys())):
        if fname in iterator.tested_factors:
            logger.info(f"跳过已测试因子: {fname} ({iterator.tested_factors[fname]})")
            continue

        print(f"\n{'━' * 90}")
        print(f"  第 {i + 1}/{len(FACTOR_POOL)} 轮 | 测试因子: {fname}")
        print(f"{'━' * 90}")

        result = iterator.test_factor(fname)
        iterator.print_iteration_result(result)

    iterator.print_status()
    iterator.print_final_report()


if __name__ == "__main__":
    main()
