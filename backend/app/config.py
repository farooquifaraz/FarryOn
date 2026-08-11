"""Application configuration.

All runtime configuration is sourced from environment variables (and an optional
``.env`` file) via :mod:`pydantic_settings`. Every field documented here has a
safe default so the service boots with zero configuration for local development
and CI. See ``.env.example`` for operator-facing documentation of each variable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
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
        default="gemini",
        description="Which AIGateway adapter to use: gemini | openai | grok | "
        "mock. Defaults to gemini — its flash model has a free tier, so a fresh "
        "deploy talks to a real model with only GEMINI_API_KEY set. NOTE: the "
        "free tier is a single per-project quota shared by ALL users, not a "
        "per-user allowance; at scale it is exhausted quickly and per-user "
        "limits (quota_enforcement_enabled / plan_limits) still apply.",
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

    # -- Live translation (hello.mode == "translate") ---------------------------
    # A translate session is a different model with a different contract — no
    # tools, no system prompt, no vision — so it gets its own provider knob
    # rather than riding on `ai_provider`. Left unset it FOLLOWS the mock
    # switch: `mock_translate` when ai_provider is mock (which is what the test
    # suite forces), `gemini_translate` otherwise. That way tests and offline
    # runs never reach for a key, and production never needs this set.
    translate_provider: str | None = Field(
        default=None,
        description="gemini_translate | mock_translate. Unset → follows "
        "AI_PROVIDER's mock switch.",
    )
    gemini_translate_model: str = Field(
        default="gemini-3.5-live-translate-preview",
        description="Google's dedicated speech-to-speech translation model. "
        "Its audio formats match ours exactly (16 kHz PCM16 in, 24 kHz out), "
        "which is why the capture/playback stack is reused unchanged.",
    )
    # Target languages the UI may request. A client-supplied code lands in the
    # upstream setup message, so it is validated against this list rather than
    # forwarded verbatim.
    # Target languages the UI may request — Google's supported list for
    # gemini-3.5-live-translate-preview, checked 2026-08-10. A client-supplied
    # code lands in the upstream setup message, so it is validated against this
    # rather than forwarded; a code the model does not know is silently replaced
    # with English, which looks exactly like the picker doing nothing.
    #
    # Kept in step with mobile/lib/features/translate/translate_languages.dart —
    # if the two drift, the app offers a language the server then refuses.
    translate_allowed_target_langs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "hi", "en", "ur", "ar", "af", "ak", "sq", "am", "hy", "az",
            "eu", "be", "bn", "bg", "my", "ca", "zh-Hans", "zh-Hant", "hr",
            "cs", "da", "nl", "et", "fil", "fi", "fr", "gl", "ka", "de",
            "el", "gu", "ha", "he", "hu", "is", "id", "it", "ja", "jv",
            "kn", "kk", "km", "rw", "ko", "lo", "lv", "lt", "mk", "ms",
            "ml", "mr", "mn", "ne", "nb", "fa", "pl", "pt-BR", "pt-PT",
            "pa", "ro", "ru", "sr", "sd", "si", "sk", "sl", "es", "su",
            "sw", "sv", "ta", "te", "th", "tr", "uk", "uz", "vi", "zu",
        ],
        description="Comma-separated BCP-47 allow-list for hello.translate.",
    )
    # Translator sessions run far longer than a Q&A session: a talk or a
    # meeting is measured in tens of minutes, where `max_session_seconds`
    # (1800) is sized for a conversation with an assistant.
    translate_max_session_seconds: int = Field(default=3600)

    # Languages to tell the model it is likely to hear. Empty (the default)
    # leaves detection entirely to the model, which is how this shipped.
    #
    # Why the lever exists: the model appears to pivot through English for a
    # non-English target. Feeding it one Arabic paragraph over a phone
    # microphone with `target=hi` produced a transcript labelled ENGLISH,
    # English prose in the heard pane, and a Hindi translation containing
    # sentences that were never spoken. The same audio with `target=en` came
    # back as correct Arabic with an accurate translation (S23, 2026-08-11).
    #
    # `AudioTranscriptionConfig` accepts `language_hints` and we had never
    # passed any. This is here to measure whether saying what is being spoken
    # stops the pivot.
    translate_language_hints: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Comma-separated BCP-47 hints for the SPOKEN language. "
        "Empty means auto-detect.",
    )

    # -- Translate: which pipeline ---------------------------------------------
    #
    # `direct` is one model doing hear-translate-speak. It is fast and, for a
    # non-English target, wrong: an Arabic paragraph came back as English
    # gibberish with invented sentences in the Hindi (S23, 2026-08-11).
    #
    # `cascade` splits the job — the live model transcribes the source, a text
    # model translates it, a TTS model speaks it. Roughly two seconds slower,
    # measurably more accurate, and each stage leaves something we can read when
    # it goes wrong. It is also what most production translators do.
    translate_pipeline: str = Field(
        default="cascade",
        description="direct (one model) | cascade (hear, translate, speak).",
    )
    translate_text_model: str = Field(
        default="gemini-3.1-flash-lite-preview",
        description="Text model for the translate step of the cascade.",
    )
    #: Must be a STREAMING TTS model. The non-streaming one returns a single
    #: blob after 8.2 s; this one starts speaking after 1.5 s and generates
    #: faster than it plays.
    translate_tts_model: str = Field(default="gemini-3.1-flash-tts-preview")
    translate_tts_voice: str = Field(default="Kore")

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

    # -- Live vision frame forwarding (cost control) ---------------------------
    # Camera frames are the single biggest Gemini Live cost driver (the model
    # re-bills the whole session history every turn, so streaming ~1 frame/sec
    # snowballs). This gate decides how many of those frames actually reach the
    # model. Vision still works in every mode — `identify_image` always reads
    # the latest cached frame, and typed turns attach a fresh frame.
    #   "continuous" — forward frames, throttled to vision_frame_min_interval_s
    #   "on_turn"    — forward at most one frame per vision_frame_heartbeat_s
    #                  (a low-rate heartbeat that keeps voice vision current)
    #   "off"        — never stream; frames are cached for identify_image only
    vision_frame_mode: Literal["continuous", "on_turn", "off"] = Field(
        default="on_turn"
    )
    # ON-DEMAND VISION (user's rule, 2026-08-05): the camera reaches the model
    # ONLY when the user asks about what is seen — i.e. through the
    # identify_image / capture_photo tools. No frame rides along with an
    # ordinary spoken or typed turn. Costs ~1 s on "what is this?" (the tool
    # round-trip) and buys: no image per "hmm", and no unprompted commentary
    # about the user ("resting your hand near your face"). Set false to go
    # back to attaching the latest frame on every turn.
    vision_on_demand_only: bool = Field(default=True)
    # Minimum seconds between two frames forwarded in "continuous" mode.
    vision_frame_min_interval_s: float = Field(default=2.0)
    # Minimum seconds between two frames forwarded in "on_turn" mode — larger,
    # since voice turns can't be detected server-side (automatic VAD lives in
    # the provider), so a slow heartbeat keeps a recent frame available.
    vision_frame_heartbeat_s: float = Field(default=6.0)

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
        description="HMAC secret for signing/verifying JWTs: the ?token= "
        "handshake on /ws/live, and (from the admin/user module onward) "
        "access + refresh tokens issued by /auth/*. Auth is best-effort on "
        "/ws/live and skipped when left at the default; the admin/user "
        "module MUST NOT be exposed with the default secret in production.",
    )
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        description="CORS allow-list. Comma-separated in the environment.",
    )

    # -- Admin/User module: auth tokens -----------------------------------
    access_token_expire_minutes: int = Field(
        default=15,
        description="Admin/User-module JWT access token lifetime.",
    )
    refresh_token_expire_days: int = Field(
        default=30,
        description="Admin/User-module refresh token lifetime.",
    )

    # -- Admin/User module: seed (first super_admin, env-driven) ----------
    first_super_admin_email: str | None = Field(
        default=None,
        description="If set (with the password below), the seed script "
        "creates/promotes this account to super_admin. Unset in production "
        "after the first successful seed run.",
    )
    first_super_admin_password: str | None = Field(default=None)

    # -- Admin/User module: SSO (Google / Microsoft OIDC) ------------------
    # Unset by default — /auth/sso/{provider}/* returns 503 SSO_NOT_CONFIGURED
    # until both id+secret for a given provider are set.
    google_client_id: str | None = Field(default=None)
    google_client_secret: str | None = Field(default=None)
    microsoft_client_id: str | None = Field(default=None)
    microsoft_client_secret: str | None = Field(default=None)
    microsoft_tenant: str = Field(
        default="common",
        description="Azure AD tenant id, or 'common' for any Microsoft account.",
    )
    sso_redirect_base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL used to build the OAuth callback redirect_uri "
        "(must exactly match a redirect URI registered with the provider).",
    )
    sso_frontend_success_url: str | None = Field(
        default=None,
        description="If set, the callback redirects here with "
        "?access_token=&refresh_token= instead of returning JSON — point "
        "this at the admin/mobile app's own callback route.",
    )
    # Required by Starlette's SessionMiddleware, which authlib's OAuth client
    # uses to store CSRF state between the /login redirect and /callback.
    # Reuses jwt_secret by default so no separate secret needs managing.
    session_secret: str | None = Field(default=None)

    # -- Admin/User module: billing webhooks --------------------------------
    # Shared secret the payment provider webhook must present in the
    # X-Webhook-Secret header. Still used by the generic /webhooks/billing/{provider}
    # path; Stripe uses its own signed webhook (stripe_webhook_secret below).
    # Webhooks are rejected (503) while this is unset.
    billing_webhook_secret: str | None = Field(default=None)

    # -- Stripe (global/USD checkout + subscriptions) -----------------------
    # Secret key (sk_test_… in test mode, sk_live_… in production). Checkout is
    # unavailable — POST /billing/checkout returns 503 — while this is unset, so
    # the app runs fine without it during local testing.
    stripe_secret_key: str | None = Field(default=None)
    # Stripe Price id (price_…) for each SOLD plan, keyed by our plan name. These
    # are created once in the Stripe dashboard (Products → Prices); Stripe is the
    # billing source of truth for the amount actually charged, our plans table
    # mirrors it for display. A plan with no mapping here can't be checked out.
    stripe_price_ids: dict[str, str] = Field(default_factory=dict)
    # Where Stripe returns the user after checkout. {CHECKOUT_SESSION_ID} in the
    # success URL is filled in by Stripe. Point these at the mobile app's deep
    # links (or a web landing) in production.
    stripe_success_url: str = Field(
        default="https://farryon.app/billing/success?session_id={CHECKOUT_SESSION_ID}"
    )
    stripe_cancel_url: str = Field(default="https://farryon.app/billing/cancel")
    # Signing secret (whsec_…) for the Stripe webhook — verifies the
    # Stripe-Signature header. Phase 3. Rejected (503) while unset.
    stripe_webhook_secret: str | None = Field(default=None)

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # -- Public site / direct APK download -------------------------------------
    # Directory holding the prebuilt Android APKs the marketing site serves
    # (FarryOn-Aurora-arm64.apk / -arm32.apk). Unset → the app falls back to the
    # repo's top-level apk/ folder (works for local dev). In production point
    # this at wherever the built APKs are placed inside the image/volume.
    apk_dir: str | None = Field(default=None)

    # -- Live session cost control (P0-2) --------------------------------------
    # Live re-bills the whole session history every turn. A sliding context
    # window caps that: past `trigger_tokens`, only the most recent
    # `target_tokens` are kept/re-billed. 8k of recent context is plenty for a
    # long conversation's memory. (It also lifts the native-audio session limit.)
    context_compression_enabled: bool = Field(default=True)
    context_trigger_tokens: int = Field(default=16000)
    context_target_tokens: int = Field(default=8000)
    # Bound runaway sessions. On reaching a limit the server sends a JSON
    # `session_expired` event and closes; the app reconnects fresh (cheap,
    # empty context). 0 disables a cap. Defaults are generous so normal use
    # is never cut off mid-conversation.
    max_session_seconds: int = Field(default=1800)     # 30 min hard cap
    idle_disconnect_seconds: int = Field(default=300)  # 5 min with no audio/text

    # -- Per-user daily quotas (cost protection) -------------------------------
    # ON by default as of Phase 4 (2026-07-20): every minute costs real Gemini
    # tokens, so shipping without caps is shipping an unbounded bill. A user with
    # no subscription is on the `free` tier (3 min/day).
    #
    # LOCAL TESTING NOTE: with this on, a test account with no subscription is
    # capped at 3 minutes of voice a day and the session ends when it's hit. To
    # test without that interruption, either set QUOTA_ENFORCEMENT_ENABLED=false
    # in backend/.env, or give the test user an active `pro` subscription row.
    quota_enforcement_enabled: bool = Field(default=True)
    # The plan a user with no active subscription falls back to. Everything the
    # app does costs us Gemini tokens on every minute of use (measured ~$0.01-
    # 0.015/active-minute, and a long session re-bills its whole context each
    # turn, so cost rises faster than time) — so "free" here is a small taste,
    # not a usable tier.
    default_plan: str = Field(default="free")

    # -- Plan catalog: THE single place to edit plans --------------------------
    # One row per plan. EVERYTHING about a plan lives here — price AND caps — so
    # changing a plan (or adding one) is a one-line edit. The billing `plans`
    # table is (re)seeded from this on every deploy, and the daily quota caps
    # (`plan_limits`, below) are DERIVED from this — you never edit those by hand.
    #
    #   price_usd     — monthly price in USD. 0.0 = free/unsold (not seeded to
    #                   the billing table, never sent to Stripe).
    #   period        — "month": caps renew daily.
    #                   "trial": `voice_minutes` is a ONE-TIME lifetime budget
    #                   (not per day), so a free taste can't be farmed forever.
    #   voice_minutes — the plan's VOICE budget in minutes. For "month" it is the
    #                   MONTHLY budget, enforced as a daily cap of minutes / 30
    #                   (so a user can't binge a whole month in one day). For
    #                   "trial" it is the whole one-time lifetime budget.
    #   image_scans / web_searches — DAILY caps. -1 = unlimited, 0 = off.
    #
    # Voice at ~$0.012/min is ~95% of cost, so `voice_minutes` is the real dial.
    # Caps are sized so a user who maxes them every day is STILL profitable — see
    # docs/REVENUE_PLAN.md for the full P&L and the no-loss rule. image_scans and
    # web_searches are one-shot and cheap (~$0.003), so their caps are about
    # abuse, not unit economics.
    plan_catalog: dict[str, dict[str, float | int | str]] = Field(
        default_factory=lambda: {
            #        price  period     voice_min  scans  searches  translate/day
            "free": {"price_usd": 0.0,  "period": "trial", "voice_minutes": 60,  "image_scans": 3,  "web_searches": 10,  "translate_minutes_per_day": 5},    # noqa: E501
            "lite": {"price_usd": 5.0,  "period": "month", "voice_minutes": 150, "image_scans": 20, "web_searches": 50,  "translate_minutes_per_day": 15},   # noqa: E501
            "plus": {"price_usd": 10.0, "period": "month", "voice_minutes": 360, "image_scans": 50, "web_searches": 100, "translate_minutes_per_day": 45},   # noqa: E501
            "pro":  {"price_usd": 20.0, "period": "month", "voice_minutes": 900, "image_scans": -1, "web_searches": 200, "translate_minutes_per_day": 120},  # noqa: E501
        }
    )
    # Days used to spread a monthly voice budget into a daily cap. 30 is the
    # conventional month; a user who talks every single day gets voice_minutes
    # of voice a month (give or take the odd 31-day month), never a month's
    # worth in one sitting.
    days_per_month: int = Field(default=30)

    # Daily quota caps the quota code reads (voice_seconds / image_scans /
    # web_searches per plan). DERIVED from `plan_catalog` on startup — leave it
    # empty and edit `plan_catalog` instead. Set it explicitly only to override
    # the derivation entirely (e.g. a test injecting a synthetic plan).
    plan_limits: dict[str, dict[str, int]] = Field(default_factory=dict)

    # -- Tunables --------------------------------------------------------------
    tool_timeout_seconds: float = Field(default=20.0)
    # Tool results are fed back into the model's context and re-billed on every
    # later turn, so an unbounded one (web_search returns tens of KB) keeps
    # costing tokens for the rest of the session. This is a BACKSTOP for tools
    # that don't limit themselves — it must sit ABOVE the largest deliberate
    # per-tool limit, or it would silently clip a tool that already sized its
    # own payload (read_email caps a full body at 4000 chars by design, and
    # halving that would break "read me the whole email"). The client UI always
    # receives the full, untruncated result either way. 0 disables the cap.
    tool_result_max_chars: int = Field(default=6000)

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

    @field_validator(
        "allowed_origins",
        "allowed_providers",
        "translate_allowed_target_langs",
        "translate_language_hints",
        mode="before",
    )
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

    @model_validator(mode="after")
    def _fill_plan_limits(self) -> "Settings":
        """Populate ``plan_limits`` from ``plan_catalog`` when left empty.

        ``plan_catalog`` is the single source of truth; the quota code reads the
        derived per-day caps here. An explicitly-provided ``plan_limits`` (e.g.
        a test override) is respected as-is.
        """
        if not self.plan_limits:
            self.plan_limits = self._derive_plan_limits()
        return self

    @property
    def auth_enabled(self) -> bool:
        """Whether JWT verification should be enforced on the WS handshake."""
        return self.jwt_secret != "dev-insecure-change-me"

    # -- Plan helpers (derived from plan_catalog) ------------------------------
    def _plan(self, name: str | None) -> dict[str, float | int | str]:
        """The catalog row for ``name``, falling back to the default plan."""
        cat = self.plan_catalog
        return cat.get(name or "") or cat.get(self.default_plan, {})

    def is_trial_plan(self, name: str | None) -> bool:
        """True when the plan's voice budget is a one-time LIFETIME total.

        The session voice meter uses this to decide whether to compare usage
        against the user's all-time voice (trial) or just today's (monthly).

        Exact lookup, no fallback: only a plan explicitly marked ``period:
        "trial"`` is a trial. An unknown plan (e.g. a custom one an operator
        added straight to the DB) is treated as monthly — the safe default, so a
        stray name never turns into a permanent lifetime lock.
        """
        row = self.plan_catalog.get(name or self.default_plan)
        return bool(row) and str(row.get("period", "month")) == "trial"

    def plan_price_cents(self, name: str) -> int:
        """The plan's monthly price in cents (0 for free/unsold)."""
        return int(round(float(self._plan(name).get("price_usd", 0)) * 100))

    def _derive_plan_limits(self) -> dict[str, dict[str, int]]:
        """Daily quota caps derived from :attr:`plan_catalog`. Per plan:

        * ``month`` — daily voice cap = ``round(voice_minutes * 60 / days_per_month)``.
        * ``trial`` — voice cap = the whole lifetime budget (``voice_minutes * 60``);
          the meter compares it against the user's LIFETIME voice, not today's
          (see ``session._load_voice_usage``), so 60 trial minutes are 60 minutes
          ever, not per day.

        ``image_scans`` / ``web_searches`` pass through as daily caps.
        """
        out: dict[str, dict[str, int]] = {}
        for name, p in self.plan_catalog.items():
            minutes = int(p.get("voice_minutes", 0))
            if str(p.get("period", "month")) == "trial":
                voice_seconds = minutes * 60
            else:
                voice_seconds = round(minutes * 60 / max(1, self.days_per_month))
            out[name] = {
                "voice_seconds": voice_seconds,
                "image_scans": int(p.get("image_scans", 0)),
                "web_searches": int(p.get("web_searches", 0)),
                # Stated per DAY in the catalog rather than derived from a
                # monthly budget like voice. Voice needs the trial/monthly
                # split because the free plan's 60 minutes are a one-time
                # allowance; giving translation a second lifetime meter would
                # mean two ways to run out, two places to get it wrong, and a
                # user who cannot tell which one stopped them.
                "translate_seconds": int(
                    p.get("translate_minutes_per_day", 0)
                ) * 60,
            }
        return out


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()
