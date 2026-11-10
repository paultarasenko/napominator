"""Application settings: environment-driven, validated at start-up (fail fast)."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from napominator.infrastructure.db.url import DEFAULT_DATABASE_URL, normalize_database_url

MIN_WEBHOOK_SECRET_LENGTH = 16


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: SecretStr = Field(min_length=10)
    database_url: str = DEFAULT_DATABASE_URL

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"

    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    dispatcher_poll_seconds: float = Field(default=5.0, gt=0, le=60)
    dispatcher_lease_seconds: int = Field(default=60, ge=10, le=600)
    user_hourly_limit: int = Field(default=60, ge=1, le=10_000)
    heartbeat_path: str = "/tmp/napominator.heartbeat"  # noqa: S108

    # Optional webhook mode (mutually exclusive with long polling).
    webhook_url: str | None = None
    webhook_secret: SecretStr | None = None
    webhook_host: str = "0.0.0.0"  # noqa: S104
    webhook_port: int = Field(default=8080, ge=1, le=65535)
    webhook_path: str = "/telegram/webhook"

    redis_url: str | None = None

    llm_enabled: bool = False
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None

    sentry_dsn: str | None = None

    @field_validator("database_url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        return value

    @field_validator("redis_url", "webhook_url", "sentry_dsn", "llm_model", "llm_base_url")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.webhook_url:
            if not self.webhook_url.startswith("https://"):
                msg = "WEBHOOK_URL must use https://"
                raise ValueError(msg)
            if (
                self.webhook_secret is None
                or len(self.webhook_secret.get_secret_value()) < MIN_WEBHOOK_SECRET_LENGTH
            ):
                msg = (
                    f"WEBHOOK_SECRET (>= {MIN_WEBHOOK_SECRET_LENGTH} chars) "
                    "is required when WEBHOOK_URL is set"
                )
                raise ValueError(msg)
        if self.llm_enabled and not (self.llm_api_key and self.llm_model):
            msg = "LLM_API_KEY and LLM_MODEL are required when LLM_ENABLED=true"
            raise ValueError(msg)
        return self

    @property
    def webhook_enabled(self) -> bool:
        return self.webhook_url is not None
