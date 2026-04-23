"""衍生品因子。"""

from pathlib import Path

import polars as pl

from src.factors.base import FactorBase
from src.factors.registry import register_factor


@register_factor
class FundingRateMAFactor(FactorBase):
    """资金费率 N 期均值。"""

    def __init__(self, period: int = 7, cache_dir: Path | None = None) -> None:
        super().__init__(cache_dir)
        self._period = period

    @property
    def name(self) -> str:
        return f"funding_rate_ma_{self._period}"

    @property
    def dependencies(self) -> list[str]:
        return ["funding_rate"]

    def compute(self, df: pl.DataFrame) -> pl.Series:
        """计算资金费率 N 期移动平均。"""
        return df["funding_rate"].rolling_mean(window_size=self._period).alias(self.name)
