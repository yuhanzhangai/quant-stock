"""策略基类。"""

from abc import ABC, abstractmethod

import pandas as pd


class StrategyBase(ABC):
    """策略抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称。"""
        ...

    @abstractmethod
    def generate_signals(
        self, price: pd.Series, **params: int | float
    ) -> tuple[pd.Series, pd.Series]:
        """生成交易信号。

        Args:
            price: 价格序列
            **params: 策略参数

        Returns:
            (entries, exits) 布尔信号
        """
        ...
