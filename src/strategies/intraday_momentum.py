"""
Strategy Status: Archive
Strategy Name: intraday_momentum
Research State: Academic-based, not validated through pipeline

日内动量效应：基于学术研究的分钟线策略。

来源: ScienceDirect 论文 "Intraday return predictability in cryptocurrency"
发现: 前几个小时的方向预测后几个小时的方向（日内动量）

核心逻辑：
- 每天看前 4 小时的方向（48 根 5m）
- 如果前 4h 涨 > 阈值，后面跟涨（日内动量）
- 如果前 4h 跌 > 阈值，不交易（避免抄底）
- 收盘前出场（避免隔夜风险）
"""

import pandas as pd
import numpy as np
from loguru import logger

from src.strategies.base import StrategyBase


class IntradayMomentumStrategy(StrategyBase):
    """日内动量策略。

    每个交易日（UTC 0:00 起算）：
    - 前 4h 涨幅 > threshold -> 做多
    - 持仓到当日结束或止损
    - 每天最多 1 笔交易
    """

    @property
    def name(self) -> str:
        return "intraday_momentum"

    def generate_signals(
        self,
        price: pd.Series,
        session_bars: int = 48,     # 前 4h = 48 根 5m
        momentum_threshold: float = 0.005,  # 前 4h 涨 > 0.5% 才入场
        day_bars: int = 288,        # 1 天 = 288 根 5m
        hold_bars: int = 192,       # 持仓 16h = 192 根 5m
        stop_pct: float = 1.0,      # 止损 1%
        **kwargs: int | float,
    ) -> tuple[pd.Series, pd.Series]:
        """生成日内动量信号。"""
        entries = pd.Series(False, index=price.index)
        exits = pd.Series(False, index=price.index)

        i = 0
        while i < len(price) - session_bars:
            # 前 session_bars 根的收益
            session_start = price.iloc[i]
            session_end = price.iloc[i + session_bars - 1]
            session_return = (session_end - session_start) / session_start

            if session_return > momentum_threshold:
                # 入场：session 结束后
                entry_idx = i + session_bars
                if entry_idx < len(price):
                    entries.iloc[entry_idx] = True

                    # 出场：持仓 hold_bars 后 或 止损
                    entry_price = price.iloc[entry_idx]
                    for j in range(entry_idx + 1, min(entry_idx + hold_bars, len(price))):
                        pnl = (price.iloc[j] - entry_price) / entry_price * 100
                        if pnl < -stop_pct:
                            exits.iloc[j] = True
                            break
                    else:
                        # 到期自动出场
                        exit_idx = min(entry_idx + hold_bars, len(price) - 1)
                        exits.iloc[exit_idx] = True

            # 跳到下一个交易日
            i += day_bars

        entries = entries.fillna(False)
        exits = exits.fillna(False)

        logger.debug(
            f"IntradayMom | session={session_bars} threshold={momentum_threshold:.3f} "
            f"hold={hold_bars} | 入场: {entries.sum()} | 出场: {exits.sum()}"
        )
        return entries, exits


def intraday_momentum_signal(
    price: pd.Series, session_bars: int = 48,
    momentum_threshold: float = 0.005, hold_bars: int = 192,
    stop_pct: float = 1.0, **kwargs: int | float,
) -> tuple[pd.Series, pd.Series]:
    return IntradayMomentumStrategy().generate_signals(
        price, session_bars=session_bars, momentum_threshold=momentum_threshold,
        hold_bars=hold_bars, stop_pct=stop_pct
    )
