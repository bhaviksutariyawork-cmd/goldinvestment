from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CCC_", extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "creative_command_center"

    # Fernet key protecting access tokens at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Absent, the app still runs but refuses to store a token.
    token_encryption_key: str | None = None

    meta_api_version: str = "v21.0"
    meta_api_base: str = "https://graph.facebook.com"
    # Ad-level pulls time out on large accounts; past this we switch to the
    # async report flow rather than retrying a request that will not finish.
    meta_sync_timeout_seconds: float = 90.0
    meta_async_poll_seconds: float = 5.0
    meta_async_max_polls: int = 120

    backfill_days: int = 90
    refresh_days: int = 7
    sync_interval_hours: int = 4
    enable_scheduler: bool = True

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
