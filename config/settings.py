"""全局配置，基于 pydantic-settings 从 .env 读取。"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class OKXSettings(BaseSettings):
    """OKX API 配置。"""

    model_config = SettingsConfigDict(env_prefix="OKX_")

    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    use_simulated: bool = False


class TelegramSettings(BaseSettings):
    """Telegram 通知配置（可选）。"""

    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    bot_token: str = ""
    chat_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


class Settings(BaseSettings):
    """全局配置入口。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 日志
    log_level: str = "INFO"

    # 数据目录
    data_dir: Path = Path("data")

    # 子配置
    okx: OKXSettings = OKXSettings()
    telegram: TelegramSettings = TelegramSettings()

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
