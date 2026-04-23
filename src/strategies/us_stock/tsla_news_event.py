"""特斯拉新闻事件驱动策略。

核心逻辑：
1. 维护特斯拉重大新闻事件目录（财报、产品、监管、CEO 言论等）
2. 在事件窗口内检测利好/利空因子
3. 根据检测结果做多或做空

利好/利空因子检测方法：
- 事件后 N 根 K 线的价格动量方向
- 成交量放大程度
- 波动率突增程度
- 综合打分判断利好/利空
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


@dataclass
class NewsEvent:
    """单条新闻事件。"""

    date: str  # UTC 日期 "YYYY-MM-DD"
    event_type: str  # earnings / product / regulatory / ceo / macro
    title: str
    expected_sentiment: str  # bullish / bearish / unknown


# =========================================================================
# 特斯拉重大新闻事件目录
# TSLA-USDT-SWAP 数据范围: 2026-02-25 ~ 至今
# 标注 expected_sentiment 用于事后验证检测准确度
# =========================================================================
TSLA_NEWS_EVENTS: list[NewsEvent] = [
    # ---- 2026 Q1 财报季 ----
    NewsEvent("2026-04-22", "earnings", "Q1 2026 财报发布", "unknown"),

    # ---- CEO / 治理 ----
    NewsEvent("2026-02-26", "ceo", "Musk DOGE 持续争议 / 欧洲抵制扩大", "bearish"),
    NewsEvent("2026-03-10", "ceo", "Musk 宣布缩减 DOGE 投入 / 回归特斯拉", "bullish"),
    NewsEvent("2026-03-25", "ceo", "Musk 确认全职回归特斯拉 CEO 职务", "bullish"),

    # ---- 产品与技术 ----
    NewsEvent("2026-03-01", "product", "Model Y Juniper 全球交付开始", "bullish"),
    NewsEvent("2026-03-15", "product", "FSD v13 大规模推送 / 安全数据公布", "bullish"),
    NewsEvent("2026-04-10", "product", "Robotaxi Austin 试运营首周数据", "unknown"),

    # ---- 监管与关税 ----
    NewsEvent("2026-03-05", "regulatory", "美国新一轮对华关税生效", "bearish"),
    NewsEvent("2026-03-20", "regulatory", "EU 确认对中国 EV 关税 / 利好特斯拉欧洲", "bullish"),
    NewsEvent("2026-04-02", "regulatory", "Trump 全面关税政策升级", "bearish"),
    NewsEvent("2026-04-09", "regulatory", "部分关税 90 天暂停", "bullish"),

    # ---- 宏观市场 ----
    NewsEvent("2026-03-08", "macro", "美股大跌 / 衰退恐慌", "bearish"),
    NewsEvent("2026-03-18", "macro", "美联储维持利率 / 鸽派表态", "bullish"),
    NewsEvent("2026-04-07", "macro", "全球股市暴跌 / 关税恐慌升级", "bearish"),
    NewsEvent("2026-04-14", "macro", "市场反弹 / 风险偏好回升", "bullish"),

    # ---- 销量与业绩 ----
    NewsEvent("2026-03-03", "earnings", "2月全球交付数据发布：同比下降", "bearish"),
    NewsEvent("2026-04-01", "earnings", "Q1 全球交付数据：低于预期", "bearish"),
]


def detect_sentiment(
    price: pd.Series,
    volume: pd.Series,
    event_ts: pd.Timestamp,
    pre_hours: int = 24,
    post_hours: int = 48,
) -> dict:
    """检测事件前后的利好/利空因子。

    Args:
        price: 收盘价序列（index 为 datetime）
        volume: 成交量序列
        event_ts: 事件时间点
        pre_hours: 事件前观察窗口（小时）
        post_hours: 事件后观察窗口（小时）

    Returns:
        因子字典，含 sentiment 评分和各子因子
    """
    pre_start = event_ts - pd.Timedelta(hours=pre_hours)
    post_end = event_ts + pd.Timedelta(hours=post_hours)

    pre_data = price[pre_start:event_ts]
    post_data = price[event_ts:post_end]
    pre_vol = volume[pre_start:event_ts]
    post_vol = volume[event_ts:post_end]

    if len(pre_data) < 3 or len(post_data) < 3:
        return {"sentiment_score": 0.0, "detected": "unknown", "valid": False}

    # --- 因子 1: 价格动量 ---
    # 事件后的价格变化百分比
    price_at_event = pre_data.iloc[-1]
    price_after = post_data.iloc[-1]
    price_change_pct = (price_after - price_at_event) / price_at_event * 100

    # 事件后 4h 的即时反应
    immediate_end = min(len(post_data), 4)
    immediate_change = (post_data.iloc[immediate_end - 1] - price_at_event) / price_at_event * 100

    # --- 因子 2: 成交量放大 ---
    avg_pre_vol = pre_vol.mean() if len(pre_vol) > 0 else 1
    avg_post_vol = post_vol.mean() if len(post_vol) > 0 else 1
    vol_ratio = avg_post_vol / max(avg_pre_vol, 1e-10)

    # --- 因子 3: 波动率突增 ---
    pre_returns = pre_data.pct_change().dropna()
    post_returns = post_data.pct_change().dropna()
    pre_volatility = pre_returns.std() if len(pre_returns) > 1 else 0.001
    post_volatility = post_returns.std() if len(post_returns) > 1 else 0.001
    vol_spike = post_volatility / max(pre_volatility, 1e-10)

    # --- 因子 4: 最大瞬间冲击 ---
    if len(post_data) > 0:
        max_up = (post_data.max() - price_at_event) / price_at_event * 100
        max_down = (post_data.min() - price_at_event) / price_at_event * 100
    else:
        max_up = max_down = 0.0

    # --- 综合评分 ---
    # 正分 = 利好，负分 = 利空
    # 权重: 即时反应(40%) + 持续动量(30%) + 量能确认(15%) + 冲击幅度(15%)
    momentum_score = np.sign(immediate_change) * min(abs(immediate_change), 10) / 10  # [-1, 1]
    trend_score = np.sign(price_change_pct) * min(abs(price_change_pct), 15) / 15
    vol_confirm = min(vol_ratio, 5) / 5  # [0, 1], 量能越大越可信
    impact_score = (max_up + max_down) / max(abs(max_up) + abs(max_down), 1e-10)  # [-1, 1]

    sentiment_score = (
        0.40 * momentum_score
        + 0.30 * trend_score
        + 0.15 * vol_confirm * np.sign(price_change_pct)
        + 0.15 * impact_score
    )

    detected = "bullish" if sentiment_score > 0.1 else ("bearish" if sentiment_score < -0.1 else "neutral")

    return {
        "sentiment_score": round(float(sentiment_score), 4),
        "detected": detected,
        "valid": True,
        "price_at_event": round(float(price_at_event), 2),
        "immediate_change_pct": round(float(immediate_change), 2),
        "total_change_pct": round(float(price_change_pct), 2),
        "vol_ratio": round(float(vol_ratio), 2),
        "vol_spike": round(float(vol_spike), 2),
        "max_up_pct": round(float(max_up), 2),
        "max_down_pct": round(float(max_down), 2),
    }


class TslaNewsEventStrategy(StrategyBase):
    """特斯拉新闻事件驱动策略（支持双向交易）。

    在重大新闻事件发生后：
    1. 用前 reaction_hours 根 K 线检测利好/利空
    2. 利好 → 做多，利空 → 做空
    3. 持仓 hold_hours 小时后强制平仓
    4. 止损 stop_pct%，止盈 take_profit_pct%
    """

    @property
    def name(self) -> str:
        return "tsla_news_event"

    def generate_signals(
        self,
        price: pd.Series,
        reaction_hours: int = 4,
        hold_hours: int = 48,
        momentum_threshold: float = 0.5,
        stop_pct: float = 3.0,
        take_profit_pct: float = 8.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成做多信号（兼容基类接口）。"""
        long_entries, long_exits, _, _ = self.generate_signals_bilateral(
            price, reaction_hours, hold_hours,
            momentum_threshold, stop_pct, take_profit_pct,
        )
        return long_entries, long_exits

    def generate_signals_bilateral(
        self,
        price: pd.Series,
        reaction_hours: int = 4,
        hold_hours: int = 48,
        momentum_threshold: float = 0.5,
        stop_pct: float = 3.0,
        take_profit_pct: float = 8.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """生成双向交易信号。

        Args:
            price: 收盘价序列
            reaction_hours: 事件后观察反应的小时数
            hold_hours: 最大持仓小时数
            momentum_threshold: 动量百分比阈值，超过才入场
            stop_pct: 止损百分比
            take_profit_pct: 止盈百分比

        Returns:
            (long_entries, long_exits, short_entries, short_exits)
        """
        long_entries = pd.Series(False, index=price.index)
        long_exits = pd.Series(False, index=price.index)
        short_entries = pd.Series(False, index=price.index)
        short_exits = pd.Series(False, index=price.index)

        if not hasattr(price.index, "tz") or price.index.tz is None:
            price_idx = price.index.tz_localize("UTC")
        else:
            price_idx = price.index

        trade_log: list[dict] = []

        for event in TSLA_NEWS_EVENTS:
            event_ts = pd.Timestamp(event.date, tz="UTC")

            # 找到事件后最近的 K 线
            mask_after_event = price_idx >= event_ts
            if not mask_after_event.any():
                continue

            first_bar_idx = mask_after_event.argmax()

            # 检查反应期
            reaction_end = min(first_bar_idx + reaction_hours, len(price) - 1)
            if reaction_end <= first_bar_idx:
                continue

            entry_price = price.iloc[first_bar_idx]
            reaction_price = price.iloc[reaction_end]
            change_pct = (reaction_price - entry_price) / entry_price * 100

            if abs(change_pct) < momentum_threshold:
                logger.debug(
                    f"跳过 {event.date} {event.title} | "
                    f"反应 {change_pct:+.2f}% < 阈值 {momentum_threshold}%"
                )
                continue

            entry_bar = reaction_end
            is_long = change_pct > 0
            entry_val = price.iloc[entry_bar]

            if is_long:
                long_entries.iloc[entry_bar] = True
            else:
                short_entries.iloc[entry_bar] = True

            # 出场逻辑
            exit_found = False
            exit_reason = "到期"
            exit_pnl = 0.0

            for j in range(entry_bar + 1, min(entry_bar + hold_hours, len(price))):
                cur_price = price.iloc[j]
                if is_long:
                    pnl_pct = (cur_price - entry_val) / entry_val * 100
                else:
                    pnl_pct = (entry_val - cur_price) / entry_val * 100

                if pnl_pct >= take_profit_pct:
                    if is_long:
                        long_exits.iloc[j] = True
                    else:
                        short_exits.iloc[j] = True
                    exit_found = True
                    exit_reason = "止盈"
                    exit_pnl = pnl_pct
                    break
                elif pnl_pct <= -stop_pct:
                    if is_long:
                        long_exits.iloc[j] = True
                    else:
                        short_exits.iloc[j] = True
                    exit_found = True
                    exit_reason = "止损"
                    exit_pnl = pnl_pct
                    break

            if not exit_found:
                force_exit = min(entry_bar + hold_hours, len(price) - 1)
                if is_long:
                    long_exits.iloc[force_exit] = True
                    exit_pnl = (price.iloc[force_exit] - entry_val) / entry_val * 100
                else:
                    short_exits.iloc[force_exit] = True
                    exit_pnl = (entry_val - price.iloc[force_exit]) / entry_val * 100

            direction = "做多" if is_long else "做空"
            trade_log.append({
                "date": event.date,
                "title": event.title,
                "direction": direction,
                "entry_price": entry_val,
                "reaction": change_pct,
                "pnl_pct": exit_pnl,
                "exit_reason": exit_reason,
            })

            logger.info(
                f"事件交易 | {event.date} | {event.title} | "
                f"反应 {change_pct:+.2f}% → {direction} | "
                f"结果: {exit_reason} {exit_pnl:+.2f}%"
            )

        self._trade_log = trade_log

        n_long = long_entries.sum()
        n_short = short_entries.sum()
        logger.info(
            f"TslaNewsEvent | 做多: {n_long} 次 | 做空: {n_short} 次 | "
            f"合计: {n_long + n_short} 次"
        )
        return long_entries, long_exits, short_entries, short_exits

    def get_trade_log(self) -> list[dict]:
        """获取交易日志。"""
        return getattr(self, "_trade_log", [])


def analyze_all_events(
    price: pd.Series,
    volume: pd.Series,
) -> pd.DataFrame:
    """分析所有新闻事件的利好/利空因子。

    Args:
        price: 收盘价序列
        volume: 成交量序列

    Returns:
        事件分析结果 DataFrame
    """
    results = []

    for event in TSLA_NEWS_EVENTS:
        event_ts = pd.Timestamp(event.date, tz="UTC")

        factors = detect_sentiment(price, volume, event_ts)
        factors["date"] = event.date
        factors["event_type"] = event.event_type
        factors["title"] = event.title
        factors["expected"] = event.expected_sentiment
        factors["correct"] = (
            factors["detected"] == event.expected_sentiment
            if event.expected_sentiment != "unknown"
            else None
        )
        results.append(factors)

    df = pd.DataFrame(results)

    # 统计检测准确率
    valid = df[df["valid"] & df["correct"].notna()]
    if len(valid) > 0:
        accuracy = valid["correct"].mean() * 100
        logger.info(
            f"利好/利空因子检测准确率: {accuracy:.1f}% "
            f"({int(valid['correct'].sum())}/{len(valid)})"
        )

    return df


def tsla_news_event_signal(
    price: pd.Series,
    reaction_hours: int = 4,
    hold_hours: int = 48,
    momentum_threshold: float = 0.5,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    """便捷函数。"""
    return TslaNewsEventStrategy().generate_signals(
        price,
        reaction_hours=reaction_hours,
        hold_hours=hold_hours,
        momentum_threshold=momentum_threshold,
    )
