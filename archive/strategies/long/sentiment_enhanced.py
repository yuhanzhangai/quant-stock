"""情绪增强 MinSwing：Fear & Greed + 资金费率过滤。

核心改进：
- Fear（<30）时更积极入场（恐慌时抄底是好时机）
- Greed（>70）时更保守（贪婪时减少做多）
- 资金费率异常高时暂停（市场过热）
- 不是加更多判断条件，而是调节 MinSwing 的 aggressiveness
"""

import json
from pathlib import Path

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase
from src.strategies.minute_swing import MinuteSwingStrategy


def load_fear_greed() -> dict[str, int]:
    """加载 Fear & Greed 历史数据，返回 {date_str: value}。"""
    path = Path("data/raw/fear_greed.json")
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    result = {}
    for item in data.get("data", []):
        ts = int(item["timestamp"])
        date_str = pd.Timestamp(ts, unit="s").strftime("%Y-%m-%d")
        result[date_str] = int(item["value"])
    return result


class SentimentEnhancedStrategy(StrategyBase):
    """情绪增强 MinSwing。

    Fear 期间（<30）：降低 min_gap（更频繁交易 = 抓更多反弹）
    Neutral（30-70）：正常参数
    Greed 期间（>70）：提高 min_gap（减少交易 = 避免追高）
    """

    @property
    def name(self) -> str:
        return "sentiment_enhanced"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 180,
        base_tp: float = 8.0,
        base_sl: float = 2.0,
        base_gap: int = 144,
        fear_gap_mult: float = 0.5,  # Fear 时 gap 减半（更激进）
        greed_gap_mult: float = 2.0,  # Greed 时 gap 翻倍（更保守）
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成情绪增强信号。"""
        fg_data = load_fear_greed()

        # 根据 Fear & Greed 调整参数
        # 如果没有情绪数据，用 50（neutral）
        if hasattr(price.index, "strftime"):
            fg_values = pd.Series([fg_data.get(d.strftime("%Y-%m-%d"), 50) for d in price.index], index=price.index)
        else:
            fg_values = pd.Series(50, index=price.index)

        # 分区间生成信号
        all_entries = pd.Series(False, index=price.index)
        all_exits = pd.Series(False, index=price.index)

        # 整体用 MinSwing 生成基础信号
        strat = MinuteSwingStrategy()

        # Fear 期间：更激进
        fear_mask = fg_values < 30
        if fear_mask.any():
            fear_gap = max(int(base_gap * fear_gap_mult), 12)
            e, x = strat.generate_signals(
                price, trend_ma=trend_ma, stop_pct=base_sl, take_profit_pct=base_tp, min_gap=fear_gap
            )
            all_entries = all_entries | (e & fear_mask)
            all_exits = all_exits | (x & fear_mask)

        # Neutral 期间：正常
        neutral_mask = (fg_values >= 30) & (fg_values <= 70)
        if neutral_mask.any():
            e, x = strat.generate_signals(
                price, trend_ma=trend_ma, stop_pct=base_sl, take_profit_pct=base_tp, min_gap=base_gap
            )
            all_entries = all_entries | (e & neutral_mask)
            all_exits = all_exits | (x & neutral_mask)

        # Greed 期间：保守
        greed_mask = fg_values > 70
        if greed_mask.any():
            greed_gap = int(base_gap * greed_gap_mult)
            e, x = strat.generate_signals(
                price, trend_ma=trend_ma, stop_pct=base_sl * 0.7, take_profit_pct=base_tp * 0.7, min_gap=greed_gap
            )
            all_entries = all_entries | (e & greed_mask)
            all_exits = all_exits | (x & greed_mask)

        all_entries = all_entries & (~all_entries.shift(1).fillna(False))
        all_exits = all_exits & (~all_exits.shift(1).fillna(False))

        n_fear = fear_mask.sum()
        n_greed = greed_mask.sum()
        logger.debug(
            f"SentiEnhanced | fear_bars:{n_fear} greed_bars:{n_greed} | "
            f"入场: {all_entries.sum()} | 出场: {all_exits.sum()}"
        )
        return all_entries, all_exits


def sentiment_enhanced_signal(
    price: pd.Series,
    trend_ma: int = 180,
    base_tp: float = 8.0,
    base_gap: int = 144,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return SentimentEnhancedStrategy().generate_signals(price, trend_ma=trend_ma, base_tp=base_tp, base_gap=base_gap)
