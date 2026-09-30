from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="CHEMO_PRODUCT_", extra="ignore"
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    host: str = "127.0.0.1"
    port: int = Field(default=8011, ge=1, le=65535)
    database_url: SecretStr | None = None
    runtime_test_database_url: SecretStr | None = None

    @property
    def read_enabled(self) -> bool:
        # 未接可信院方认证前，仅本机开发与测试允许浏览草稿目录。
        return self.environment in {"local", "test"}
