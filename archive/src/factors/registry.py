"""因子注册表。"""

from pathlib import Path

import polars as pl
from loguru import logger

from src.factors.base import FactorBase

_REGISTRY: dict[str, type[FactorBase]] = {}


def register_factor(cls: type[FactorBase]) -> type[FactorBase]:
    """注册因子的装饰器。

    Args:
        cls: 因子类

    Returns:
        原始因子类（不修改）
    """
    # 实例化一次获取 name
    instance = cls()
    _REGISTRY[instance.name] = cls
    logger.debug(f"因子注册: {instance.name}")
    return cls


def list_factors() -> list[str]:
    """列出所有已注册因子名称。"""
    return list(_REGISTRY.keys())


def get_factor(name: str, cache_dir: Path | None = None) -> FactorBase:
    """获取指定因子实例。

    Args:
        name: 因子名称
        cache_dir: 缓存目录

    Returns:
        因子实例

    Raises:
        KeyError: 因子未注册
    """
    if name not in _REGISTRY:
        raise KeyError(f"因子 '{name}' 未注册，可用: {list_factors()}")
    return _REGISTRY[name](cache_dir=cache_dir)


def compute_all(
    df: pl.DataFrame,
    symbol: str = "",
    timeframe: str = "",
    cache_dir: Path | None = None,
    factor_names: list[str] | None = None,
) -> pl.DataFrame:
    """批量计算所有（或指定的）因子。

    Args:
        df: 输入数据
        symbol: 交易对（用于缓存）
        timeframe: 时间周期（用于缓存）
        cache_dir: 缓存目录
        factor_names: 指定计算的因子名，None 表示全部

    Returns:
        包含所有因子列的 DataFrame
    """
    names = factor_names or list_factors()
    result = df.clone()

    for name in names:
        factor = get_factor(name, cache_dir=cache_dir)

        # 检查依赖列
        missing = [dep for dep in factor.dependencies if dep not in result.columns]
        if missing:
            logger.warning(f"因子 {name} 缺少依赖列: {missing}，跳过")
            continue

        if cache_dir and symbol and timeframe:
            series = factor.compute_cached(result, symbol, timeframe)
        else:
            series = factor.compute(result)

        result = result.with_columns(series.alias(name))

    logger.info(f"批量因子计算完成 | 计算: {len(names)} 个")
    return result
