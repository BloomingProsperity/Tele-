from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    payment_listen_host: str = Field(default="0.0.0.0", validation_alias="PAYMENT_LISTEN_HOST")
    payment_listen_port: int = Field(default=8090, validation_alias="PAYMENT_LISTEN_PORT")
    payment_state_db_path: Path = Field(
        default=Path(".payment_bridge.db"),
        validation_alias="PAYMENT_STATE_DB_PATH",
    )
    payment_webhook_secret: str | None = Field(default=None, validation_alias="PAYMENT_WEBHOOK_SECRET")
    payment_admin_secret: str | None = Field(default=None, validation_alias="PAYMENT_ADMIN_SECRET")

    sub2api_base_url: str = Field(validation_alias="SUB2API_BASE_URL")
    sub2api_admin_api_key: str = Field(validation_alias="SUB2API_ADMIN_API_KEY")
    sub2api_timeout_seconds: float = Field(default=10.0, validation_alias="SUB2API_TIMEOUT_SECONDS")
    recharge_code_prefix: str = Field(default="s2p_", validation_alias="RECHARGE_CODE_PREFIX")

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @field_validator("payment_listen_host", "sub2api_admin_api_key", "recharge_code_prefix")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field must not be empty.")
        return stripped

    @field_validator("payment_webhook_secret", "payment_admin_secret")
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("sub2api_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        stripped = value.strip().rstrip("/")
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("SUB2API_BASE_URL must start with http:// or https://")
        return stripped

    @field_validator("payment_listen_port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if value <= 0 or value > 65535:
            raise ValueError("PAYMENT_LISTEN_PORT must be in range 1..65535.")
        return value

    @field_validator("sub2api_timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("SUB2API_TIMEOUT_SECONDS must be > 0.")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of: {', '.join(sorted(allowed))}.")
        return normalized


def load_settings() -> Settings:
    return Settings()

