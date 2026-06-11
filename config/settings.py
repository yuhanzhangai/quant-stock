"""全局配置，基于 pydantic-settings 从 .env 读取。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置入口，直接从 .env 读取所有变量。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 日志
    log_level: str = "INFO"

    # 数据目录
    data_dir: Path = Path("data")

    # 执行层硬钉：永远只跑模拟盘（paper），C5+ 启动断言用，严禁真实下单
    paper_only: bool = True

    # stock-picker-mcp 价格库（只读信号源）
    prices_db_path: Path = Path.home() / ".stock-picker-mcp" / "prices.db"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "research.duckdb"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "meta.sqlite"

    # ── 执行层 ledger(ORDER_LEDGER_SPEC r3 §2:分库 + 读写分离)──
    @property
    def execution_ledger_path(self) -> Path:
        return self.data_dir / "execution" / "ledger.duckdb"

    @property
    def execution_export_dir(self) -> Path:
        """writer 每循环 parquet 快照落点;Dash 只读这里,永不直连 ledger.duckdb。"""
        return self.data_dir / "execution" / "export"


# 全局单例
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
