from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    run_mode: str = Field(default="userbot", validation_alias="RUN_MODE")

    tg_api_id: int | None = Field(default=None, validation_alias="TG_API_ID")
    tg_api_hash: str | None = Field(default=None, validation_alias="TG_API_HASH")
    tg_session_name: str = Field(default="tele_ai_userbot", validation_alias="TG_SESSION_NAME")
    tg_phone: str | None = Field(default=None, validation_alias="TG_PHONE")
    bot_token: str | None = Field(default=None, validation_alias="BOT_TOKEN")

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
    incoming_translation_output_mode: str = Field(
        default="saved_messages",
        validation_alias="INCOMING_TRANSLATION_OUTPUT_MODE",
    )
    enable_groups: bool = Field(default=True, validation_alias="ENABLE_GROUPS")
    enable_private: bool = Field(default=True, validation_alias="ENABLE_PRIVATE")
    global_pause: bool = Field(default=False, validation_alias="GLOBAL_PAUSE")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    state_db_path: Path = Field(default=Path(".tele_ai_state.db"), validation_alias="STATE_DB_PATH")
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
        value = value.strip().lower()
        if not value:
            raise ValueError("DEFAULT_TARGET_LANG must not be empty.")
        return value

    @field_validator("run_mode")
    @classmethod
    def validate_run_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"userbot", "bot", "both"}:
            raise ValueError("RUN_MODE must be 'userbot', 'bot', or 'both'.")
        return normalized

    @field_validator("incoming_translation_output_mode")
    @classmethod
    def validate_incoming_output_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"saved_messages", "same_chat", "off"}:
            raise ValueError(
                "INCOMING_TRANSLATION_OUTPUT_MODE must be one of: saved_messages, same_chat, off."
            )
        return normalized

    @field_validator("nvidia_model", "nvidia_fallback_model")
    @classmethod
    def validate_required_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Model name must not be empty.")
        return value

    @field_validator("tg_api_hash", "bot_token", "kimi_api_key", "kimi_base_url", "kimi_model")
    @classmethod
    def normalize_optional_str(cls, value: str | None) -> str | None:
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

    @field_validator("lang_history_limit")
    @classmethod
    def validate_lang_history_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("LANG_HISTORY_LIMIT must be > 0.")
        return value

    @field_validator(
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

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> "Settings":
        if self.run_mode in {"userbot", "both"}:
            if self.tg_api_id is None:
                raise ValueError("TG_API_ID is required when RUN_MODE=userbot/both.")
            if not self.tg_api_hash:
                raise ValueError("TG_API_HASH is required when RUN_MODE=userbot/both.")
        if self.run_mode in {"bot", "both"}:
            if not self.bot_token:
                raise ValueError("BOT_TOKEN is required when RUN_MODE=bot/both.")
        return self


def load_settings() -> Settings:
    return Settings()
