"""交易时段策略：利用不同时段的行为差异。

Crypto 虽然 24h 交易，但不同时段特征不同：
- 亚洲时段 (UTC 0-8): 通常波动较小，适合均值回归
- 欧洲时段 (UTC 8-16): 波动开始增加，趋势启动
- 美洲时段 (UTC 16-24): 波动最大，流动性最好

策略：在欧洲开盘时看方向，如果与趋势一致则入场。
"""

import pandas as pd
from loguru import logger

from src.strategies.base import StrategyBase


class SessionTradingStrategy(StrategyBase):
    """交易时段策略。

    每天 UTC 8:00（欧洲开盘）检查：
    - 亚洲时段方向（UTC 0-8 涨跌）
    - 如果亚洲涨 + 大趋势向上 -> 在欧洲开盘做多
    - 在美洲收盘前（UTC 23:00）出场
    - 每天最多 1 笔
    """

    @property
    def name(self) -> str:
        return "session_trading"

    def generate_signals(
        self,
        price: pd.Series,
        trend_ma: int = 200,
        asia_threshold: float = 0.003,  # 亚洲时段涨 > 0.3%
        stop_pct: float = 1.5,
        take_profit_pct: float = 3.0,
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成时段信号。"""
        ma = price.rolling(window=trend_ma).mean()
        uptrend = price > ma

        entries = pd.Series(False, index=price.index)
        exits = pd.Series(False, index=price.index)

        # 需要 datetime index
        if not hasattr(price.index, 'hour'):
            # 无时间信息，使用模拟：每 288 根（1天）为一周期
            day_bars = 288  # 5m * 288 = 24h
            asia_bars = 96  # 8h
            eu_start = 96   # UTC 8:00

            i = 0
            while i + day_bars < len(price):
                # 亚洲时段收益
                asia_ret = (price.iloc[i + asia_bars] - price.iloc[i]) / price.iloc[i]

                # 欧洲开盘入场点
                entry_idx = i + eu_start
                if entry_idx < len(price) and asia_ret > asia_threshold and uptrend.iloc[entry_idx]:
                    entries.iloc[entry_idx] = True

                    # 出场：当天结束 或 止损/止盈
                    entry_price = price.iloc[entry_idx]
                    for j in range(entry_idx + 1, min(i + day_bars, len(price))):
                        pnl = (price.iloc[j] - entry_price) / entry_price * 100
                        if pnl < -stop_pct or pnl > take_profit_pct:
                            exits.iloc[j] = True
                            break
                    else:
                        exit_idx = min(i + day_bars - 1, len(price) - 1)
                        exits.iloc[exit_idx] = True

                i += day_bars
        else:
            # 有真实时间戳
            for i in range(len(price)):
                hour = price.index[i].hour
                if hour == 8:  # 欧洲开盘
                    # 找当天亚洲时段起点（UTC 0:00）
                    asia_start_idx = max(0, i - 96)
                    asia_ret = (price.iloc[i] - price.iloc[asia_start_idx]) / price.iloc[asia_start_idx]
                    if asia_ret > asia_threshold and uptrend.iloc[i]:
                        entries.iloc[i] = True

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"Session | asia_thr={asia_threshold} | "
            f"入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def session_trading_signal(
    price: pd.Series, asia_threshold: float = 0.003,
    stop_pct: float = 1.5, take_profit_pct: float = 3.0,
    **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return SessionTradingStrategy().generate_signals(
        price, asia_threshold=asia_threshold, stop_pct=stop_pct,
        take_profit_pct=take_profit_pct
    )
