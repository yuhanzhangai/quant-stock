"""技术因子库。"""

import math
from pathlib import Path
from typing import Optional

import polars as pl

from src.factors.base import FactorBase
from src.factors.registry import register_factor


@register_factor
class MomentumFactor(FactorBase):
    """N 日收益率（动量因子）。"""

    def __init__(self, period: int = 20, cache_dir: Optional[Path] = None) -> None:
        super().__init__(cache_dir)
        self._period = period

    @property
    def name(self) -> str:
        return f"momentum_{self._period}"

    @property
    def dependencies(self) -> list[str]:
        return ["close"]

    def compute(self, df: pl.DataFrame) -> pl.Series:
        """计算 N 日收益率。"""
        return (
            df["close"].pct_change(self._period)
        ).alias(self.name)


@register_factor
class VolatilityFactor(FactorBase):
    """N 日年化波动率。"""

    def __init__(self, period: int = 20, cache_dir: Optional[Path] = None) -> None:
        super().__init__(cache_dir)
        self._period = period

    @property
    def name(self) -> str:
        return f"volatility_{self._period}"

    @property
    def dependencies(self) -> list[str]:
        return ["close"]

    def compute(self, df: pl.DataFrame) -> pl.Series:
        """计算 N 日年化波动率（基于对数收益）。"""
        log_returns = df["close"].log().diff()
        rolling_std = log_returns.rolling_std(window_size=self._period)
        # 年化：假设 1h 数据，365 * 24 = 8760 小时
        annualized = rolling_std * math.sqrt(8760)
        return annualized.alias(self.name)


@register_factor
class RSIFactor(FactorBase):
    """相对强弱指数 (RSI)。"""

    def __init__(self, period: int = 14, cache_dir: Optional[Path] = None) -> None:
        super().__init__(cache_dir)
        self._period = period

    @property
    def name(self) -> str:
        return f"rsi_{self._period}"

    @property
    def dependencies(self) -> list[str]:
        return ["close"]

    def compute(self, df: pl.DataFrame) -> pl.Series:
        """计算 RSI。"""
        delta = df["close"].diff()

        gains = delta.clip(lower_bound=0)
        losses = (-delta).clip(lower_bound=0)

        avg_gain = gains.rolling_mean(window_size=self._period)
        avg_loss = losses.rolling_mean(window_size=self._period)

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.alias(self.name)


@register_factor
class VolumeZScoreFactor(FactorBase):
    """成交量 Z-Score。"""

    def __init__(self, period: int = 20, cache_dir: Optional[Path] = None) -> None:
        super().__init__(cache_dir)
        self._period = period

    @property
    def name(self) -> str:
        return f"volume_zscore_{self._period}"

    @property
    def dependencies(self) -> list[str]:
        return ["volume"]

    def compute(self, df: pl.DataFrame) -> pl.Series:
        """计算成交量 Z-Score。"""
        vol_mean = df["volume"].rolling_mean(window_size=self._period)
        vol_std = df["volume"].rolling_std(window_size=self._period)
        zscore = (df["volume"] - vol_mean) / vol_std
        return zscore.alias(self.name)


@register_factor
class ATRFactor(FactorBase):
    """平均真实波幅 (ATR)。"""

    def __init__(self, period: int = 14, cache_dir: Optional[Path] = None) -> None:
        super().__init__(cache_dir)
        self._period = period

    @property
    def name(self) -> str:
        return f"atr_{self._period}"

    @property
    def dependencies(self) -> list[str]:
        return ["high", "low", "close"]

    def compute(self, df: pl.DataFrame) -> pl.Series:
        """计算 ATR。"""
        prev_close = df["close"].shift(1)

        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()

        # 取三者最大值作为 True Range
        tr_df = pl.DataFrame({"tr1": tr1, "tr2": tr2, "tr3": tr3})
        tr = tr_df.select(pl.max_horizontal("tr1", "tr2", "tr3")).to_series()
        atr = tr.rolling_mean(window_size=self._period)
        return atr.alias(self.name)
