"""Application configuration.

All runtime configuration is sourced from environment variables (and an optional
``.env`` file) via :mod:`pydantic_settings`. Every field documented here has a
safe default so the service boots with zero configuration for local development
and CI. See ``.env.example`` for operator-facing documentation of each variable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AIProvider = Literal["gemini", "openai", "mock"]


class Settings(BaseSettings):
    """Strongly-typed application settings loaded from the environment.

    Instances are cached via :func:`get_settings`; treat them as immutable for
    the lifetime of the process.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- AI provider selection -------------------------------------------------
    ai_provider: AIProvider = Field(
        default="mock",
        description="Which AIGateway adapter to use: gemini | openai | mock.",
    )

    # Gemini Live
    gemini_api_key: str | None = Field(default=None)
    gemini_model: str = Field(default="gemini-2.0-flash-live-001")

    # OpenAI Realtime
    openai_api_key: str | None = Field(default=None)
    openai_realtime_model: str = Field(default="gpt-4o-realtime-preview")

    # -- Persistence -----------------------------------------------------------
    database_url: str = Field(
        default="sqlite+aiosqlite:///./farryon.db",
        description="Async SQLAlchemy URL. Use sqlite+aiosqlite for dev, "
        "postgresql+asyncpg://... for production.",
    )

    # -- Web search tool -------------------------------------------------------
    web_search_api_key: str | None = Field(default=None)
    web_search_provider: str = Field(
        default="mock",
        description="Web search backend: mock | tavily | serpapi.",
    )

    # -- Observability ---------------------------------------------------------
    log_level: str = Field(default="INFO")

    # -- Security / HTTP -------------------------------------------------------
    jwt_secret: str = Field(
        default="dev-insecure-change-me",
        description="HMAC secret for verifying the ?token= JWT on /ws/live. "
        "Auth is best-effort in dev and skipped when left at the default.",
    )
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        description="CORS allow-list. Comma-separated in the environment.",
    )

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # -- Tunables --------------------------------------------------------------
    tool_timeout_seconds: float = Field(default=20.0)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Allow ``ALLOWED_ORIGINS`` to be a comma-separated string."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def auth_enabled(self) -> bool:
        """Whether JWT verification should be enforced on the WS handshake."""
        return self.jwt_secret != "dev-insecure-change-me"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()
