from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    TELEGRAM_BOT_TOKEN: SecretStr
    TELEGRAM_BOT_USERNAME: str

    DATABASE_URL: str = "sqlite+aiosqlite:///./logoust.db"

    LOG_FORMAT: str = "text"
    LOG_LEVEL: str = "INFO"
    LOG_FILE_ENABLED: bool = False
    LOG_DIR: str = "./logs"
    LOG_FILE_BACKUP_DAYS: int = 7


settings = Settings()  # type: ignore[call-arg]
