"""config.settings 回归测试 — 新增字段默认值 + 路径解析与旧行为一致 + 旧字段保留。"""

from pathlib import Path

import pytest

from config.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_paper_only_default_true():
    """执行层硬钉：paper_only 默认必须为 True。"""
    assert Settings().paper_only is True


def test_research_ledger_path_is_project_root_absolute():
    """research_ledger_path 基于项目根绝对化，与 CWD 无关。"""
    s = Settings()
    assert s.research_ledger_path.is_absolute()
    assert s.research_ledger_path == PROJECT_ROOT / "data" / "meta" / "research.duckdb"


def test_research_ledger_path_matches_legacy_behavior(monkeypatch):
    """仓库根下运行时，解析结果必须与旧 CWD 相对路径 data/meta/research.duckdb 一致。"""
    monkeypatch.chdir(PROJECT_ROOT)
    legacy = Path("data/meta/research.duckdb").resolve()
    assert Settings().research_ledger_path.resolve() == legacy


def test_prices_db_path_default():
    """prices_db_path 默认指向 stock-picker-mcp 价格库（只读信号源）。"""
    assert Settings().prices_db_path == Path.home() / ".stock-picker-mcp" / "prices.db"


def test_legacy_fields_still_present():
    """旧字段（okx/telegram/log/data_dir）与衍生属性一律不动。"""
    fields = Settings.model_fields
    for name in [
        "okx_api_key",
        "okx_api_secret",
        "okx_passphrase",
        "okx_use_simulated",
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


def test_connect_research_db_uses_settings_path(monkeypatch, tmp_path):
    """src.research.db 必须读 settings.research_ledger_path；缺库时 fail-fast 且报错含该路径。"""
    from src.research import db

    fake_path = tmp_path / "nonexistent" / "research.duckdb"

    class _FakeSettings:
        research_ledger_path = fake_path

    monkeypatch.setattr(db, "get_settings", lambda: _FakeSettings())

    with pytest.raises(db.ResearchDBUnavailable, match="research.duckdb not found"):
        db.connect_research_db(required=True)

    assert db.connect_research_db(required=False) is None
