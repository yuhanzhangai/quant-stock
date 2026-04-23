"""技术因子测试。"""

import polars as pl
import pytest

from src.factors.registry import compute_all, get_factor, list_factors

# 导入以触发注册
import src.factors.technical  # noqa: F401
import src.factors.derivatives  # noqa: F401


@pytest.fixture
def sample_ohlcv() -> pl.DataFrame:
    """生成测试用 OHLCV 数据（50行）。"""
    import random
    random.seed(42)

    n = 50
    base_price = 42000.0
    prices = []
    for i in range(n):
        base_price += random.uniform(-200, 200)
        prices.append(base_price)

    return pl.DataFrame({
        "timestamp": list(range(1704067200000, 1704067200000 + n * 3600000, 3600000)),
        "open": prices,
        "high": [p + random.uniform(50, 300) for p in prices],
        "low": [p - random.uniform(50, 300) for p in prices],
        "close": [p + random.uniform(-100, 100) for p in prices],
        "volume": [random.uniform(100, 1000) for _ in range(n)],
        "symbol": ["BTC-USDT"] * n,
    })


class TestFactorRegistry:
    def test_list_factors(self) -> None:
        """应至少有 6 个注册因子。"""
        factors = list_factors()
        assert len(factors) >= 6

    def test_get_factor(self) -> None:
        """获取已注册因子。"""
        factor = get_factor("momentum_20")
        assert factor.name == "momentum_20"

    def test_get_unknown_factor(self) -> None:
        """获取未注册因子应抛异常。"""
        with pytest.raises(KeyError):
            get_factor("nonexistent_factor")


class TestMomentum:
    def test_compute(self, sample_ohlcv: pl.DataFrame) -> None:
        factor = get_factor("momentum_20")
        result = factor.compute(sample_ohlcv)
        assert len(result) == len(sample_ohlcv)
        # 前 20 个应为 null
        assert result[:20].null_count() == 20
        # 后面应有非 null 值
        assert result[20:].null_count() < len(result[20:])


class TestVolatility:
    def test_compute(self, sample_ohlcv: pl.DataFrame) -> None:
        factor = get_factor("volatility_20")
        result = factor.compute(sample_ohlcv)
        assert len(result) == len(sample_ohlcv)
        # 波动率应为正数（忽略 null）
        non_null = result.drop_nulls()
        assert (non_null > 0).all()


class TestRSI:
    def test_compute(self, sample_ohlcv: pl.DataFrame) -> None:
        factor = get_factor("rsi_14")
        result = factor.compute(sample_ohlcv)
        assert len(result) == len(sample_ohlcv)
        # RSI 应在 0-100 之间
        non_null = result.drop_nulls()
        assert (non_null >= 0).all()
        assert (non_null <= 100).all()


class TestVolumeZScore:
    def test_compute(self, sample_ohlcv: pl.DataFrame) -> None:
        factor = get_factor("volume_zscore_20")
        result = factor.compute(sample_ohlcv)
        assert len(result) == len(sample_ohlcv)


class TestATR:
    def test_compute(self, sample_ohlcv: pl.DataFrame) -> None:
        factor = get_factor("atr_14")
        result = factor.compute(sample_ohlcv)
        assert len(result) == len(sample_ohlcv)
        # ATR 应为正数
        non_null = result.drop_nulls()
        assert (non_null > 0).all()


class TestComputeAll:
    def test_compute_all(self, sample_ohlcv: pl.DataFrame) -> None:
        """批量计算所有可用因子。"""
        result = compute_all(sample_ohlcv)
        # 应至少新增 5 列（排除 funding_rate_ma 因为缺少 funding_rate 列）
        new_cols = [c for c in result.columns if c not in sample_ohlcv.columns]
        assert len(new_cols) >= 5

    def test_compute_specific(self, sample_ohlcv: pl.DataFrame) -> None:
        """计算指定因子。"""
        result = compute_all(
            sample_ohlcv, factor_names=["momentum_20", "rsi_14"]
        )
        assert "momentum_20" in result.columns
        assert "rsi_14" in result.columns


class TestFactorCache:
    def test_cache_write_and_read(self, sample_ohlcv: pl.DataFrame, tmp_path: pl.DataFrame) -> None:
        """因子缓存写入和读取。"""
        factor = get_factor("momentum_20", cache_dir=tmp_path)

        # 第一次计算：写缓存
        result1 = factor.compute_cached(sample_ohlcv, "BTC-USDT", "1h")
        assert len(result1) == len(sample_ohlcv)

        # 第二次计算：应命中缓存
        result2 = factor.compute_cached(sample_ohlcv, "BTC-USDT", "1h")
        assert len(result2) == len(sample_ohlcv)

        # 缓存文件应存在
        cache_file = tmp_path / "momentum_20" / "BTC-USDT_1h.parquet"
        assert cache_file.exists()
