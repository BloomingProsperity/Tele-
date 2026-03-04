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

    discord_bot_token: str = Field(validation_alias="DISCORD_BOT_TOKEN")
    discord_command_prefix: str = Field(default="!", validation_alias="DISCORD_COMMAND_PREFIX")
    discord_owner_id: int | None = Field(default=None, validation_alias="DISCORD_OWNER_ID")
    discord_enable_guilds: bool = Field(default=True, validation_alias="DISCORD_ENABLE_GUILDS")
    discord_enable_dms: bool = Field(default=True, validation_alias="DISCORD_ENABLE_DMS")

    nvidia_api_key: str = Field(validation_alias="NVIDIA_API_KEY")
    nvidia_base_url: str = Field(validation_alias="NVIDIA_BASE_URL")
    nvidia_model: str = Field(validation_alias="NVIDIA_MODEL")
    nvidia_fallback_model: str = Field(
        default="moonshotai/kimi-k2-instruct",
        validation_alias="NVIDIA_FALLBACK_MODEL",
    )

    kimi_api_key: str | None = Field(default=None, validation_alias="KIMI_API_KEY")
    kimi_base_url: str | None = Field(default=None, validation_alias="KIMI_BASE_URL")
    kimi_model: str | None = Field(default=None, validation_alias="KIMI_MODEL")

    default_target_lang: str = Field(default="en", validation_alias="DEFAULT_TARGET_LANG")
    lang_confidence_threshold: float = Field(default=0.70, validation_alias="LANG_CONFIDENCE_THRESHOLD")
    max_text_chars: int = Field(default=4000, validation_alias="MAX_TEXT_CHARS")
    translation_prefix: str = Field(default="[AI-TR]", validation_alias="TRANSLATION_PREFIX")
    global_pause: bool = Field(default=False, validation_alias="GLOBAL_PAUSE")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    state_db_path: Path = Field(default=Path(".discord_ai_state.db"), validation_alias="DISCORD_STATE_DB_PATH")
    send_interval_seconds: float = Field(default=1.0, validation_alias="SEND_INTERVAL_SECONDS")
    lang_history_limit: int = Field(default=8, validation_alias="LANG_HISTORY_LIMIT")
    lang_history_retention_hours: int = Field(
        default=24, validation_alias="LANG_HISTORY_RETENTION_HOURS"
    )
    state_cleanup_interval_minutes: int = Field(
        default=60, validation_alias="STATE_CLEANUP_INTERVAL_MINUTES"
    )
    processed_cache_ttl_seconds: int = Field(
        default=600, validation_alias="PROCESSED_CACHE_TTL_SECONDS"
    )
    processed_cache_maxsize: int = Field(default=20000, validation_alias="PROCESSED_CACHE_MAXSIZE")
    failure_notice_ttl_seconds: int = Field(
        default=120, validation_alias="FAILURE_NOTICE_TTL_SECONDS"
    )
    failure_notice_maxsize: int = Field(default=2000, validation_alias="FAILURE_NOTICE_MAXSIZE")
    command_cooldown_seconds: float = Field(default=5.0, validation_alias="COMMAND_COOLDOWN_SECONDS")

    @field_validator("default_target_lang")
    @classmethod
    def validate_default_target_lang(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("DEFAULT_TARGET_LANG must not be empty.")
        return normalized

    @field_validator("discord_command_prefix")
    @classmethod
    def validate_discord_command_prefix(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("DISCORD_COMMAND_PREFIX must not be empty.")
        if " " in stripped:
            raise ValueError("DISCORD_COMMAND_PREFIX cannot contain spaces.")
        return stripped

    @field_validator("discord_bot_token", "nvidia_api_key", "nvidia_base_url", "nvidia_model", "nvidia_fallback_model")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Required string field must not be empty.")
        return stripped

    @field_validator("kimi_api_key", "kimi_base_url", "kimi_model")
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("lang_confidence_threshold")
    @classmethod
    def validate_confidence_threshold(cls, value: float) -> float:
        if value < 0.0 or value > 1.0:
            raise ValueError("LANG_CONFIDENCE_THRESHOLD must be between 0 and 1.")
        return value

    @field_validator("max_text_chars")
    @classmethod
    def validate_max_text_chars(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("MAX_TEXT_CHARS must be positive.")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of: {', '.join(sorted(allowed))}.")
        return normalized

    @field_validator("send_interval_seconds", "command_cooldown_seconds")
    @classmethod
    def validate_non_negative_float(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Value must be >= 0.")
        return value

    @field_validator(
        "lang_history_limit",
        "lang_history_retention_hours",
        "state_cleanup_interval_minutes",
        "processed_cache_ttl_seconds",
        "processed_cache_maxsize",
        "failure_notice_ttl_seconds",
        "failure_notice_maxsize",
    )
    @classmethod
    def validate_positive_ints(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Value must be > 0.")
        return value


def load_settings() -> Settings:
    return Settings()

