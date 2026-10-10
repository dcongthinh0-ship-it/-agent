from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CHEMO_PRODUCT_", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    host: str = "127.0.0.1"
    port: int = Field(default=8011, ge=1, le=65535)
    database_url: SecretStr | None = None
    runtime_test_database_url: SecretStr | None = None
    # Empty until the user configures their own funded model service.
    model_enabled: bool = False
    model_api_key: SecretStr | None = None
    model_base_url: str | None = None
    model_name: str | None = None
    reviewer_model_name: str | None = None
    model_timeout_seconds: int = Field(default=120, ge=1, le=600)
    model_max_turns: int = Field(default=8, ge=1, le=20)
    model_max_budget_usd: float = Field(default=0.5, gt=0, le=20)
    # Local/test launch tokens are signed server-side and scoped to hospital/doctor.
    launch_signing_key: SecretStr | None = None
    trusted_host_origins: list[str] = Field(default_factory=list)
    hospital_adapter_config: str | None = None
    hospital_delivery_config: str | None = None
    context_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    worker_poll_seconds: float = Field(default=1, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=180, ge=30, le=900)
    worker_enabled: bool = True
    worker_restart_max_seconds: float = Field(default=30, ge=1, le=300)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_directory: Path | None = Path("logs")
    log_to_file: bool = True
    log_max_bytes: int = Field(default=10_485_760, ge=1024, le=104_857_600)
    log_backup_count: int = Field(default=5, ge=1, le=30)

    @property
    def read_enabled(self) -> bool:
        # Public test browsing does not grant access to patient operations.
        return self.environment in {"local", "test"}

    @property
    def model_configured(self) -> bool:
        return bool(
            self.model_enabled
            and self.model_api_key
            and self.model_api_key.get_secret_value().strip()
            and self.model_name
        )
