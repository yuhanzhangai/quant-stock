"""config.settings 回归测试 — 字段默认值 + 路径解析 + 保留字段不丢。"""

from pathlib import Path

from config.settings import Settings


def test_paper_only_default_true():
    """执行层硬钉：paper_only 默认必须为 True。"""
    assert Settings().paper_only is True


def test_prices_db_path_default():
    """prices_db_path 默认指向 stock-picker-mcp 价格库（只读信号源）。"""
    assert Settings().prices_db_path == Path.home() / ".stock-picker-mcp" / "prices.db"


def test_legacy_fields_still_present():
    """保留字段（telegram/log/data_dir）与衍生属性一律不动。"""
    fields = Settings.model_fields
    for name in [
        "telegram_bot_token",
        "telegram_chat_id",
        "log_level",
        "data_dir",
    ]:
        assert name in fields, f"旧字段 {name} 丢失"

    s = Settings()
    assert s.data_dir == Path("data")
    assert s.log_level == "INFO"
    assert s.parquet_dir == Path("data") / "parquet"
    assert s.raw_dir == Path("data") / "raw"
    assert s.duckdb_path == Path("data") / "research.duckdb"
    assert s.sqlite_path == Path("data") / "meta.sqlite"
    assert s.telegram_enabled is False
