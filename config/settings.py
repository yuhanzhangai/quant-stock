"""全局配置，基于 pydantic-settings 从 .env 读取。"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置入口，直接从 .env 读取所有变量。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OKX API
    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""
    okx_use_simulated: bool = False

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 日志
    log_level: str = "INFO"

    # 数据目录
    data_dir: Path = Path("data")

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


# 全局单例
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取全局配置单例。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
