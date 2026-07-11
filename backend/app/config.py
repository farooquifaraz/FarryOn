"""Application configuration.

All runtime configuration is sourced from environment variables (and an optional
``.env`` file) via :mod:`pydantic_settings`. Every field documented here has a
safe default so the service boots with zero configuration for local development
and CI. See ``.env.example`` for operator-facing documentation of each variable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AIProvider = Literal["gemini", "openai", "grok", "mock"]


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
    gemini_model: str = Field(default="gemini-2.5-flash-native-audio-latest")

    # OpenAI Realtime. Use the GA model id — the old "gpt-4o-realtime-preview"
    # now returns 4004 model_not_found and only wasted a connect attempt.
    openai_api_key: str | None = Field(default=None)
    openai_realtime_model: str = Field(default="gpt-realtime")

    # Grok / xAI Realtime (OpenAI Realtime-compatible; only the endpoint differs)
    grok_api_key: str | None = Field(default=None)
    grok_realtime_model: str = Field(default="grok-realtime")

    # Providers a client is allowed to request per-session via hello.provider.
    allowed_providers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["gemini", "openai", "grok", "mock"],
        description="Comma-separated allow-list for hello.provider.",
    )

    # -- Persistence -----------------------------------------------------------
    database_url: str = Field(
        default="sqlite+aiosqlite:///./farryon.db",
        description="Async SQLAlchemy URL. Use sqlite+aiosqlite for dev, "
        "postgresql+asyncpg://... for production.",
    )

    # -- Vision / image understanding (landmark + product finder) --------------
    # Google Cloud Vision API key (LANDMARK_DETECTION + WEB_DETECTION). Required
    # for the `identify_image` tool and the `POST /detect` endpoint. The Gemini
    # key above (`gemini_api_key`) is reused for the optional product AI
    # explanation, so no separate key is needed for that.
    vision_api_key: str | None = Field(default=None)

    # -- Web search tool -------------------------------------------------------
    web_search_api_key: str | None = Field(default=None)
    web_search_provider: str = Field(
        default="mock",
        description="Primary web search backend: mock | tavily | serper | "
        "serpapi.",
    )
    # Optional second provider: used automatically when the primary errors or
    # runs out of free credits (HTTP 401/402/429). Lets you chain two free
    # tiers — e.g. tavily then serper — to maximise free usage.
    web_search_fallback_provider: str | None = Field(default=None)
    web_search_fallback_api_key: str | None = Field(default=None)

    # -- Messaging (WhatsApp / Telegram) ---------------------------------------
    # Telegram Bot API token from @BotFather. When set, send_telegram can send
    # messages directly to users who have started the bot; without it the tool
    # falls back to a t.me deep-link the user opens themselves.
    telegram_bot_token: str | None = Field(default=None)
    # Telegram USER account (MTProto via Telethon) — send to ANYONE in the
    # user's contacts with no /start needed. api_id/api_hash from
    # my.telegram.org; session is produced by the one-time login.
    telegram_api_id: int | None = Field(default=None)
    telegram_api_hash: str | None = Field(default=None)
    telegram_session: str | None = Field(default=None)
    # WhatsApp Business Cloud API (optional Phase 2 — fully automated sending).
    # Without these, send_whatsapp uses a free wa.me deep-link (1-tap send).
    whatsapp_token: str | None = Field(default=None)
    whatsapp_phone_id: str | None = Field(default=None)
    # Default country code used to normalise phone numbers (UAE=971, India=91).
    default_country_code: str = Field(default="971")

    # -- Observability ---------------------------------------------------------
    log_level: str = Field(default="INFO")

    # -- Security / HTTP -------------------------------------------------------
    jwt_secret: str = Field(
        default="dev-insecure-change-me",
        description="HMAC secret for verifying the ?token= JWT on /ws/live. "
        "Auth is best-effort in dev and skipped when left at the default.",
    )
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        description="CORS allow-list. Comma-separated in the environment.",
    )

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # -- Tunables --------------------------------------------------------------
    tool_timeout_seconds: float = Field(default=20.0)

    # -- Camera capture (identify_image / capture_photo) ------------------------
    # How long a vision tool waits for a fresh camera frame before giving up.
    # Phone cameras stream ~1 fps, so the wait normally resolves in ~1 s and
    # this value only caps the failure path.
    frame_wait_seconds: float = Field(
        default=8.0,
        description="Max seconds a vision tool waits for a fresh camera frame "
        "on a streaming (phone) camera.",
    )
    # Smart glasses are photo-trigger only: capture (~2.2-2.4 s, firmware-fixed)
    # plus the BLE thumbnail transfer. In the Glasses Lab (no other radio use)
    # that transfer is 3.1-4.6 s, but in a LIVE voice session the glasses' A2DP
    # audio link (TTS out) contends for the same 2.4 GHz radio and the transfer
    # balloons to 10-12 s typical (measured 2026-07-11). The budget must outlast
    # that so a genuine, if slow, photo is never cut off — the success path is
    # event-driven (the frame wakes the wait early), so a longer budget only
    # affects the failure backstop.
    glasses_frame_wait_seconds: float = Field(
        default=18.0,
        description="Max seconds a vision tool waits for a fresh camera frame "
        "when the active camera is smart glasses (photo-trigger capture).",
    )
    # After a one-shot photo arrives, capture_photo pauses this long before
    # returning its result — which is what triggers the model to generate its
    # "describe what you see" reply. The pause lets the model's realtime-video
    # pipeline actually ingest the just-sent frame first; without it the model
    # answers before it has "seen" the photo and hallucinates (device-proven
    # 2026-07-11). Only affects the one-shot glasses path, not phone streaming.
    frame_ingest_seconds: float = Field(
        default=1.2,
        description="Pause after a one-shot photo arrives before returning "
        "capture_photo, so the model ingests the frame before replying.",
    )

    @field_validator("allowed_origins", "allowed_providers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allow comma-separated env strings for list fields."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def _empty_int_to_none(cls, value: object) -> object:
        """Treat an empty ``TELEGRAM_API_ID=`` env value as unset (None)."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def auth_enabled(self) -> bool:
        """Whether JWT verification should be enforced on the WS handshake."""
        return self.jwt_secret != "dev-insecure-change-me"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()
