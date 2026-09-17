"""The :class:`Session` drives one ``/ws/live`` connection.

Responsibilities (per ``PROTOCOL.md`` sections 3-6):

- Perform the handshake: read ``hello`` + ``config``, emit ``ready``.
- Run two concurrent pumps until the socket closes or either side errors:
    * **read pump** — reads frames from the client socket; binary frames are
      decoded and routed to the gateway (audio/video), text frames are parsed as
      JSON control messages (``text``, ``audio_start``/``audio_stop``,
      ``interrupt``, ``ping``, ...).
    * **event pump** — consumes :class:`~app.ai.events.GatewayEvent` objects and
      translates each into the matching server message: ``transcript``,
      ``audio_start``/``audio_end`` plus ``0x03`` OUTPUT_AUDIO binary frames,
      ``state``, and tool-call lifecycle (delegated to the orchestrator).
- Handle **barge-in**: a client ``interrupt`` cancels in-flight TTS/generation.
- Persist session/transcript/audit rows and emit Prometheus metrics.
- Cancel cleanly on disconnect (both pumps + the gateway are torn down).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from app.agent.orchestrator import Orchestrator
from app.agent.tool_engine import ToolEngine
from app.ai.base import AIGateway
from app.ai.factory import build_translate_gateway
from app.ai.events import (
    AudioChunkEvent,
    AudioEndEvent,
    AudioStartEvent,
    ErrorEvent,
    EventType,
    GatewayEvent,
    ToolCallEvent,
    TranscriptEvent,
    TurnCompleteEvent,
)
from app.config import Settings, get_settings
from app.ai.transcript_refiner import (
    MIN_AUDIO_SECONDS,
    clip_to_budget,
    refine_transcript,
    seconds_of,
)
from app.ws.audio_dump import AudioDump
from app.core.account import token_rejection
from app.db import repo
from app.prompts.system import build_system_prompt
from app.db.base import get_sessionmaker
from app.db.models import User
from app.tools.quota import plan_cap, user_key_for
from app.logging_conf import get_logger
from app.observability import metrics
from app.ws.frames import FrameTag, decode_frame, encode_frame

logger = get_logger(__name__)

PROTOCOL_VERSION = 1

#: Mic audio is PCM16 LE mono at 16 kHz — fixed by PROTOCOL.md §"mic in", not
#: negotiated. The client's `config` message is informational, so the byte count
#: is the only honest source of duration: trusting a client-declared sample rate
#: would let a client under-report its own bill.
_MIC_BYTES_PER_SECOND = 16_000 * 2

#: How much speech to accumulate before writing it down. A DB round-trip per
#: audio frame would be one every 20-100 ms per live session; a crash loses at
#: most this much unbilled, which is the right side of that trade.
_VOICE_FLUSH_EVERY_S = 15.0

#: How long to wait before retrying a usage write that failed.
#:
#: A failed flush deliberately keeps its seconds PENDING so a later flush still
#: bills them — but pending stays above the flush threshold, so without a
#: backoff the very next audio frame tries again, and the next, and the next.
#: Device-seen 2026-08-10: one translate session against a database missing a
#: column produced **2021 failed queries**, roughly fifty a second, for as long
#: as it ran. Postgres would have felt that a great deal more than SQLite did.
_USAGE_RETRY_AFTER_S = 30.0

# A live microphone normally produces one frame every 20-100 ms.  Keep a
# short, bounded buffer between the WebSocket reader and an upstream provider:
# the reader must stay available to receive fresh speech even when one send is
# briefly slow, but an unbounded queue would turn a transient outage into a
# delayed replay of stale speech.
_AUDIO_FORWARD_QUEUE_MAX = 50

# Audio dumping is diagnostic-only.  A separate, bounded queue guarantees a
# slow disk can never take the live microphone path down with it.
_AUDIO_DUMP_QUEUE_MAX = 200


class AudioBackpressureError(RuntimeError):
    """The upstream audio connection cannot keep up with real-time speech."""

#: After a resumed connect, an `end_session` with no user turn heard is
#: refused for this long (see Orchestrator.resume_guard_until).
_RESUME_GUARD_S = 30.0

#: When the operator was last mailed about a provider outage (monotonic).
#: One mail per _OUTAGE_ALERT_INTERVAL_S per process, whatever the traffic —
#: every failed connect in an outage looks the same.
_OUTAGE_ALERTED_AT: float = 0.0
_OUTAGE_ALERT_INTERVAL_S = 30 * 60

#: Messages the app shows verbatim on the "service unavailable" overlay.
_PROVIDER_CREDITS_MSG = (
    "Farry's voice service is temporarily unavailable. Please try again in a "
    "little while."
)
_PROVIDER_DOWN_MSG = (
    "Farry's voice service could not start. Please try again in a moment."
)


def classify_provider_failure(exc: BaseException) -> tuple[str, str]:
    """Turn a model-connect failure into an error code + a sentence for a person.

    ``provider_credits`` — the operator's account is out of credit / over its
    quota (Gemini says ``1011 … prepayment credits are depleted``, or 429 /
    RESOURCE_EXHAUSTED). Nothing the user can do; the app must stop retrying
    and say so. ``provider_unavailable`` — anything else that stopped the
    connect. Until 2026-09-13 both reached the app as the raw exception repr
    and the client kept reconnecting into the same failure, so a depleted
    balance showed as a "connecting" spinner that never ended.
    """
    text = repr(exc).lower()
    credit_markers = (
        "credits are depleted",
        "prepayment",
        "1011",
        "429",
        "resource_exhausted",
        "quota",
        "billing",
    )
    if any(m in text for m in credit_markers):
        return "provider_credits", _PROVIDER_CREDITS_MSG
    return "provider_unavailable", _PROVIDER_DOWN_MSG


#: Last Gemini session-resumption handle per authed user, with the monotonic
#: time it was issued. A NEW session within the TTL re-attaches the previous
#: conversation's context — so an idle-expired session or a network drop no
#: longer wipes Farry's memory (Faraz's "after long time... no response, and
#: everything forgotten" report, 2026-08-27). In-memory on purpose: handles are
#: short-lived provider state, not durable user data.
_RESUME_HANDLES: dict[int, tuple[str, float]] = {}
_RESUME_TTL_S = 30 * 60.0


class Session:
    """Owns the lifecycle and concurrency for a single live connection."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        gateway_factory: Callable[..., AIGateway],
        engine: ToolEngine,
        settings: Settings,
        claims: dict[str, Any] | None = None,
    ) -> None:
        self._ws = websocket
        # The handshake token's claims, signature/exp already verified by
        # ws/live.py::_resolve_claims. None = nobody signed in, which only
        # reaches here on a local run (production rejects the connection).
        # Kept whole rather than reduced to an id because `iat` is needed too:
        # a token minted before the account's force-logout watermark is dead
        # even though it parses (see app/core/account.py).
        self._claims = claims
        self._authed_user_id = int(claims["sub"]) if claims else None
        # The gateway is built AFTER the handshake, once we know which provider
        # the client asked for (hello.provider) — see :meth:`_resolve_provider`.
        self._gateway_factory = gateway_factory
        self._gateway: AIGateway | None = None
        self._engine = engine
        self._settings = settings

        self.session_id: str = uuid.uuid4().hex
        self.resume_of: str | None = None
        self._user_id: int | None = None
        # The caps-bearing plan for this session's user, resolved once in
        # _load_voice_usage. None until then; _meter_voice falls back to the
        # global default via plan_cap(plan=None) if enforcement somehow runs
        # first.
        self._plan_name: str | None = None

        self._send_lock = asyncio.Lock()
        self._closing = False
        #: Manual activity detection for this session (glasses mic): the
        #: client's speech_start/speech_end drive the model's turn window.
        #: Decided from hello, applied to the gateway before connect.
        self._manual_vad = False
        #: A manual activity window is open (activityStart sent, no end yet).
        self._activity_open = False
        #: Mic frames dropped because the upstream sender fell behind
        #: (oldest-first). Logged, never fatal — see _queue_audio.
        self._audio_dropped = 0
        self._audio_drop_logged_at = 0.0
        self._hello: dict[str, Any] | None = None
        # Session mode, resolved from hello: "agent" (the assistant, and the
        # default for every client that has never heard of this field) or
        # "translate" (continuous speech-to-speech translation). Translate
        # sessions run a different model with a different contract, so the
        # branches read off this rather than sniffing the gateway.
        self._mode: str = "agent"
        self._translate: dict[str, Any] = {}
        self._orchestrator: Orchestrator | None = None
        # Tracks active tool-call tasks so they are awaited/cancelled cleanly.
        self._tool_tasks: set[asyncio.Task[Any]] = set()
        # Monotonic time the last video frame was actually forwarded to the
        # model — drives the cost-saving frame gate (see _handle_binary).
        self._last_video_sent: float = 0.0
        # Frame accounting for cost visibility: how many video frames the client
        # sent vs how many actually reached the model (the gate drops the rest).
        self._frames_in_video: int = 0
        self._frames_sent_video: int = 0
        # Turn timing (agent mode). Monotonic anchors for the per-turn
        # "turn.timing" log line and the farryon_turn_* histograms — the
        # instrumentation that answers "when did Farry hear me, and how long
        # was I waiting?". `_t_user_last` is the newest user-transcript delta,
        # the closest observable stand-in for "the user stopped speaking".
        self._turn_index: int = 0
        #: When the last INPUT_AUDIO frame arrived — drives the audio.resumed
        #: gap log (input-side latency evidence).
        self._last_audio_frame_at: float = 0.0
        #: Off unless a dump directory is configured; a copy of exactly the
        #: audio the transcriber was given, for when the transcript is wrong.
        self._audio_dump = AudioDump(
            getattr(settings, "debug_audio_dump_dir", ""), self.session_id
        )
        # Audio forwarding and diagnostic writing intentionally run outside
        # the WebSocket read pump.  Before this separation, one slow Gemini
        # send, DB commit, or WAV write delayed every later microphone frame.
        self._audio_queue: asyncio.Queue[tuple[bytes, int | None] | None] = (
            asyncio.Queue(maxsize=_AUDIO_FORWARD_QUEUE_MAX)
        )
        self._audio_sender_task: asyncio.Task[None] | None = None
        self._audio_dump_queue: asyncio.Queue[bytes | None] | None = None
        self._audio_dump_task: asyncio.Task[None] | None = None
        #: The current user turn's microphone audio, kept so a second reader
        #: can correct the words on screen once the reply has begun. The
        #: client mutes its mic while the assistant speaks, so everything that
        #: arrives between one reply and the next is the user's utterance.
        self._turn_pcm = bytearray()
        #: What each turn's user utterance has come to so far, keyed by turn:
        #: the saved row id once the Live final has been stored, and/or the
        #: refined text once the second reading has landed. The two arrive in
        #: EITHER order — a short reply finishes before a 3-second second
        #: reading — and a one-shot flag saved both and then ate the next
        #: turn's text (device-seen 2026-09-07). Whichever comes second
        #: corrects, never duplicates.
        self._turn_rows: dict[int, int] = {}
        self._turn_refined_text: dict[int, str] = {}
        #: Which turn the audio in `_turn_pcm` belongs to — it is the turn
        #: whose reply has not started yet, which is the current index.
        self._refine_task: asyncio.Task[None] | None = None
        self._t_user_first: float = 0.0
        self._t_user_last: float = 0.0
        self._t_reply_started: float = 0.0
        self._t_first_audio_sent: float = 0.0
        self._turn_tools: int = 0
        #: When mic audio first arrived with nobody heard and nobody speaking
        #: (0 = not in such a stretch). See ``_note_unheard_audio``.
        self._unheard_audio_since: float = 0.0
        #: Silence filler (see ``_maybe_fill_silence``): the burst the filler
        #: last completed, so it runs once per burst and never during one.
        self._filler_task: asyncio.Task[None] | None = None
        self._filler_filled_for: float = 0.0
        #: Quiet-triggered nudge bookkeeping: the last frame time a quiet
        #: nudge was sent for, so each pause nudges at most once.
        self._quiet_nudged_for: float = 0.0
        self._turn_watch_task: asyncio.Task[None] | None = None
        #: Consecutive nudges (either kind) with nothing heard since. Reset
        #: the moment the provider transcribes the user.
        self._cap_nudges: int = 0
        self._quiet_nudges: int = 0
        # Cost caps: session start + last real user activity (audio/text), used
        # by the watchdog to end runaway or forgotten-open sessions.
        self._session_started: float = time.monotonic()
        self._last_activity: float = time.monotonic()
        # Voice metering. Mic audio is the most expensive thing this product
        # does and was the one metered resource nothing counted: the plans have
        # sold `voice_seconds` caps (free 300 / pro 900) since they were written,
        # while `check_quota` only ever ran for image_scans and web_searches, so
        # `daily_usage.voice_seconds` sat at 0 forever and the cap could never
        # fire. Counted here rather than in a tool because audio arrives as raw
        # frames, with no ToolContext to hang a check on.
        #
        # `_voice_used_s` is today's total, read once at session start;
        # `_voice_pending_s` is what this session has added since the last flush.
        # Kept apart so the DB is written every _VOICE_FLUSH_EVERY_S of speech
        # rather than on every ~20-100 ms frame.
        self._voice_used_s: float = 0.0
        self._voice_pending_s: float = 0.0
        self._voice_capped: bool = False
        # Monotonic time before which a failed usage write must not be retried.
        self._voice_flush_retry_at: float = 0.0
        self._voice_flush_task: asyncio.Task[None] | None = None
        self._voice_flush_lock = asyncio.Lock()
        self._translate_flush_retry_at: float = 0.0
        self._translate_flush_task: asyncio.Task[None] | None = None
        self._translate_flush_lock = asyncio.Lock()
        # Translation metering — the same three counters, kept apart from the
        # voice ones so neither can spend the other's budget. `_translate_warned`
        # makes the 80% heads-up fire once rather than on every frame.
        self._translate_used_s: float = 0.0
        self._translate_pending_s: float = 0.0
        self._translate_capped: bool = False
        self._translate_warned: bool = False
        # Set only once the ACTIVE gauge has actually been incremented, so a
        # session that dies during the handshake cannot decrement a gauge it
        # never raised and drive it negative.
        self._translate_gauge_held: bool = False

    # -- Public entrypoint ----------------------------------------------------

    async def run(self) -> None:
        """Handshake, then run both pumps until disconnect; always clean up."""
        reason = "normal"
        try:
            if not await self._handshake():
                reason = "handshake_failed"
                return

            # A spent budget is answered BEFORE any provider is connected: a
            # user past their trial used to get a full Gemini session (paid
            # for by the operator) that died on its first audio frame, and
            # could type to it for free for as long as they liked.
            if self._mode != "translate":
                await self._load_voice_usage()
                if await self._refuse_if_budget_spent():
                    reason = "quota_exceeded"
                    return

            # Now that hello has arrived, build the gateway for the requested
            # provider (or the server default), giving the model the user's
            # local time so reminders resolve in their timezone.
            client_time = (self._hello or {}).get("clientTime")
            languages = (self._hello or {}).get("languages")
            # Cap what goes into the prompt: two names, short, non-empty —
            # hello is client-supplied and this string lands verbatim in the
            # system instruction.
            langs = None
            if isinstance(languages, list):
                langs = [
                    str(l).strip()[:30] for l in languages[:2] if str(l).strip()
                ] or None
            prompt = build_system_prompt(
                client_time if isinstance(client_time, str) else None,
                languages=langs,
            )
            if self._mode == "translate":
                # Built here, not through `_gateway_factory`: the factory's job
                # is to hand a provider the tools and system prompt, and a
                # translate model takes neither. Routing it through anyway
                # would mean a translate adapter that quietly accepts both.
                #
                # Construction itself can fail — an unknown TRANSLATE_PROVIDER,
                # or a provider module this build does not ship yet. That has
                # to be answered, not dropped: an unexplained dead socket is
                # the one outcome a user cannot act on.
                try:
                    self._gateway = build_translate_gateway(
                        self._settings,
                        target_language=self._translate["target_language"],
                        echo_target_language=self._translate[
                            "echo_target_language"
                        ],
                        speak_on_device=self._translate["speak_on_device"],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "translate.gateway_unavailable",
                        session_id=self.session_id,
                        error=repr(exc),
                    )
                    await self._send_error(
                        "translate_unavailable",
                        "Live translation is not available on this server.",
                        fatal=True,
                    )
                    reason = "translate_unavailable"
                    return
            else:
                provider = self._resolve_provider()
                options = self._provider_options(provider)
                self._gateway = (
                    self._gateway_factory(provider, prompt, options)
                    if options
                    else self._gateway_factory(provider, prompt)
                )
                self._wire_session_resume()
                self._apply_vad_mode()

            try:
                await self._gateway.connect()
            except Exception as exc:  # noqa: BLE001 - surface provider failures
                logger.error(
                    "gateway.connect_failed",
                    session_id=self.session_id,
                    provider=self._gateway.provider,
                    model=self._gateway.model_label,
                    error=repr(exc),
                )
                # A translate session must NEVER fall back to an agent
                # gateway. The fallback below exists so a dead provider still
                # leaves you with a working assistant — but here the user asked
                # to be translated, and handing them an assistant that answers
                # the room instead is worse than telling them it failed.
                if self._mode == "translate":
                    await self._send_error(
                        "translate_unavailable",
                        "Live translation could not start. Try again in a "
                        "moment.",
                        fatal=True,
                    )
                    reason = "connect_failed"
                    return
                # CHANGED (UX Spec BUG 2): if the client REQUESTED a specific
                # provider (e.g. openai/grok) and it fails to connect — bad key,
                # wrong model id, endpoint down — don't leave the user with a dead
                # session. Fall back to the server's configured default provider
                # (Gemini is the recommended default for full voice+vision) so the
                # assistant still works. Only fall back to a DIFFERENT provider.
                default_provider = self._settings.ai_provider
                if self._gateway.provider != default_provider:
                    logger.warning(
                        "gateway.fallback",
                        session_id=self.session_id,
                        from_provider=self._gateway.provider,
                        to=default_provider,
                    )
                    try:
                        self._gateway = self._gateway_factory(
                            default_provider, prompt
                        )
                        self._wire_session_resume()
                        self._apply_vad_mode()
                        await self._gateway.connect()
                    except Exception as exc2:  # noqa: BLE001
                        await self._report_provider_failure(exc2)
                        reason = "connect_failed"
                        return
                    # Non-fatal heads-up so the app can show which model is live.
                    await self._send_error(
                        "provider_fallback",
                        f"Requested AI provider was unavailable; switched to "
                        f"{self._gateway.model_label}.",
                        fatal=False,
                    )
                else:
                    await self._report_provider_failure(exc)
                    reason = "connect_failed"
                    return
            # Resume insurance: the resumed context's TAIL is the previous
            # conversation's last exchange — often "end the session" / a
            # confirmed action. Without this note the model can re-act on that
            # stale instruction the moment fresh audio arrives (suspected on
            # device 2026-08-27: end_session fired 20s into a resumed session
            # with no turn heard). Silent: turn_complete=False never speaks.
            if getattr(self, "_resumed_from_handle", False):
                if self._orchestrator is not None:
                    self._orchestrator.resume_guard_until = (
                        time.monotonic() + _RESUME_GUARD_S
                    )
                note_fn = getattr(self._gateway, "send_silent_note", None)
                if note_fn is not None:
                    await note_fn(
                        "(System note: this is a NEW connection that resumed "
                        "the previous conversation's context for continuity. "
                        "Every request before this note is ALREADY handled — "
                        "do NOT re-execute any earlier instruction (ending "
                        "the session, recording, sending, or any other tool "
                        "call) unless the user asks again AFTER this note. "
                        "Wait for the user to speak. Do not respond to this "
                        "note.)"
                    )
                    logger.info(
                        "session.resume_note_sent", session_id=self.session_id
                    )
            if not await self._persist_session_start():
                # Suspended, deleted, or force-logged-out since the token was
                # minted. Say so and close rather than send `ready`: the app
                # treats a dead session as its cue to sign out, and a session
                # that can't own anything has nothing to offer anyway.
                await self._send_error(
                    "session_rejected",
                    "This account is no longer active. Sign in again.",
                    fatal=True,
                )
                reason = "rejected"
                return
            # AFTER the owner is resolved, never before: _usage_key() is built
            # from _user_id, which _persist_session_start is what sets. Loading
            # first would read (and later bill) a key made from the session id,
            # so every session would start from zero and the daily cap would
            # never be reached.
            if self._mode == "translate":
                await self._load_translate_usage()
                metrics.TRANSLATE_SESSIONS.labels(
                    target=self._translate["target_language"]
                ).inc()
                metrics.TRANSLATE_ACTIVE.inc()
                self._translate_gauge_held = True
            await self._send_json(
                {
                    "type": "ready",
                    "sessionId": self.session_id,
                    "protocolVersion": PROTOCOL_VERSION,
                    "model": self._gateway.model_label,
                    # Echoed so the client can *verify* it got the mode it
                    # asked for rather than assume it. A translate screen that
                    # silently attached to an assistant would look like a
                    # translator producing very strange translations.
                    "mode": self._mode,
                    **(
                        {"targetLanguage": self._translate["target_language"]}
                        if self._mode == "translate"
                        else {}
                    ),
                }
            )
            await self._send_state("listening")

            # No orchestrator in translate mode: no tools, no tool audit rows,
            # no web-search/email/location config reaching a model that has no
            # way to use them. Vision is refused separately and explicitly in
            # `_handle_binary` — a None orchestrator alone would not have
            # stopped a frame reaching the gateway.
            if self._mode != "translate":
                web_search = (self._hello or {}).get("webSearch")
                email = (self._hello or {}).get("email")
                emails = (self._hello or {}).get("emails")
                location = (self._hello or {}).get("location")
                # Vision tools wait longer for a frame on photo-trigger glasses
                # than on a streaming phone camera (see Settings for the budgets).
                device = (self._hello or {}).get("device")
                device_kind = (
                    device.get("kind") if isinstance(device, dict) else None
                )
                frame_wait_seconds = self._frame_wait_for_kind(device_kind)
                # Size the gateway's frame-freshness window to the camera too, so a
                # batching adapter (OpenAI) keeps a slow glasses still instead of
                # dropping it. No-op for streaming adapters (Gemini).
                self._gateway.set_camera_kind(device_kind)
                self._orchestrator = Orchestrator(
                    engine=self._engine,
                    gateway=self._gateway,
                    sessionmaker=get_sessionmaker(),
                    notify_client=self._send_json,
                    session_id=self.session_id,
                    user_id=self._user_id,
                    web_search=web_search
                    if isinstance(web_search, dict)
                    else None,
                    email=email if isinstance(email, dict) else None,
                    emails=[e for e in emails if isinstance(e, dict)]
                    if isinstance(emails, list)
                    else None,
                    location=location if isinstance(location, dict) else None,
                    frame_wait_seconds=frame_wait_seconds,
                )

            # Start these only after the gateway is connected.  The read pump
            # can now keep accepting microphone frames while the sender waits
            # for provider I/O; diagnostic disk writes get their own worker.
            self._audio_sender_task = asyncio.create_task(
                self._audio_sender(), name="audio_sender"
            )
            if self._mode == "agent" and float(
                getattr(self._settings, "vad_silence_filler_seconds", 0.0) or 0.0
            ) > 0.0:
                self._filler_task = asyncio.create_task(
                    self._silence_filler(), name="silence_filler"
                )
            if self._mode == "agent" and float(
                getattr(self._settings, "stuck_turn_quiet_nudge_seconds", 0.0) or 0.0
            ) > 0.0:
                self._turn_watch_task = asyncio.create_task(
                    self._turn_watch(), name="turn_watch"
                )
            warm = float(getattr(self._settings, "vad_warmup_seconds", 0.0) or 0.0)
            if self._mode == "agent" and warm > 0.0:
                # Prime the provider's detector with the quiet before the
                # first words (see Settings.vad_warmup_seconds). Not metered,
                # not dumped: it is not the user's audio.
                chunk = bytes(16_000 * 2 // 10)
                for _ in range(max(1, int(round(warm * 10)))):
                    await self._queue_audio(chunk, None)
                logger.info("audio.warmup_sent", session_id=self.session_id, ms=int(warm * 1000))
            if self._audio_dump.enabled:
                self._audio_dump_queue = asyncio.Queue(
                    maxsize=_AUDIO_DUMP_QUEUE_MAX
                )
                self._audio_dump_task = asyncio.create_task(
                    self._audio_dump_writer(), name="audio_dump_writer"
                )

            read_task = asyncio.create_task(self._read_pump(), name="read_pump")
            event_task = asyncio.create_task(
                self._event_pump(), name="event_pump"
            )
            watchdog_task = asyncio.create_task(
                self._session_watchdog(), name="watchdog"
            )
            done, pending = await asyncio.wait(
                {read_task, event_task, watchdog_task, self._audio_sender_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            # Surface a non-cancellation error from whichever pump finished.
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    reason = "error"
                    logger.error(
                        "session.pump_error",
                        session_id=self.session_id,
                        error=str(exc),
                    )
        except WebSocketDisconnect:
            reason = "client_disconnect"
        except Exception as exc:  # noqa: BLE001 - top-level safety net
            reason = "error"
            logger.error(
                "session.error", session_id=self.session_id, error=str(exc)
            )
        finally:
            await self._cleanup(reason)

    # -- Provider selection ---------------------------------------------------

    def _provider_options(self, provider: str | None) -> dict | None:
        """The user's own keys for the ``cascade`` provider (Dev Mode).

        ``hello.devKeys`` is ``{"stt": "...", "llm": "..."}``; blanks are
        dropped so the server's key stands in for anything not supplied. Read
        for ``cascade`` only — no other provider takes client keys — and never
        logged (the hello dict itself is never logged either).
        """
        if provider != "cascade":
            return None
        out: dict = {}
        # The user's Settings languages → speech-to-text hints (script!).
        languages = (self._hello or {}).get("languages")
        if isinstance(languages, list):
            langs = [str(l).strip()[:30] for l in languages[:2] if str(l).strip()]
            if langs:
                out["languages"] = langs
        keys = (self._hello or {}).get("devKeys")
        if isinstance(keys, dict):
            for wire, opt in (("stt", "stt_api_key"), ("llm", "llm_api_key")):
                v = keys.get(wire)
                if isinstance(v, str) and v.strip():
                    out[opt] = v.strip()
            if "stt_api_key" in out or "llm_api_key" in out:
                logger.info(
                    "provider.dev_keys",
                    session_id=self.session_id,
                    stt="stt_api_key" in out,
                    llm="llm_api_key" in out,
                )
        return out or None

    def _resolve_provider(self) -> str | None:
        """Pick the provider from ``hello.provider`` if allowed, else default.

        Returns ``None`` to let the factory fall back to ``settings.ai_provider``
        (the server default) when the client did not request a valid provider.
        """
        requested = (self._hello or {}).get("provider")
        if isinstance(requested, str):
            requested = requested.strip().lower()
            if requested in self._settings.allowed_providers:
                logger.info(
                    "provider.selected",
                    session_id=self.session_id,
                    provider=requested,
                )
                return requested
            if requested:
                logger.warning(
                    "provider.not_allowed",
                    session_id=self.session_id,
                    requested=requested,
                )
        return None

    def _wire_session_resume(self) -> None:
        """Hand a resumption-capable gateway this user's last handle.

        Duck-typed: only a gateway exposing ``resume_handle`` (Gemini) takes
        part — mock/OpenAI/grok are untouched. Keyed by the AUTHED user id so a
        handle can never leak one user's conversation into another's session;
        anonymous sessions get no continuity. Within the TTL, the new Gemini
        connection re-attaches the previous conversation's context, so a
        reconnect or an idle-expiry restart continues where the user left off.
        """
        uid = self._authed_user_id
        gw = self._gateway
        if uid is None or not hasattr(gw, "resume_handle"):
            return
        if not getattr(self._settings, "session_resume_enabled", True):
            logger.info("session.resume_disabled", session_id=self.session_id)
            return
        entry = _RESUME_HANDLES.get(uid)
        if entry is not None and time.monotonic() - entry[1] < _RESUME_TTL_S:
            gw.resume_handle = entry[0]
            self._resumed_from_handle = True
            logger.info(
                "session.resume_handle_applied",
                session_id=self.session_id,
                age_s=int(time.monotonic() - entry[1]),
            )

        def _keep(handle: str, _uid: int = uid) -> None:
            first = _uid not in _RESUME_HANDLES
            _RESUME_HANDLES[_uid] = (handle, time.monotonic())
            if first:
                # Once per user per process: proves updates are FLOWING (their
                # absence at 17:51 on 2026-08-27 was undiagnosable without it).
                logger.info(
                    "session.resume_handle_stored", session_id=self.session_id
                )

        gw.on_resume_handle = _keep

    # -- Handshake ------------------------------------------------------------

    async def _handshake(self) -> bool:
        """Read ``hello`` (and optional ``config``); returns success.

        ``config`` is accepted but informational — the wire formats are fixed by
        ``PROTOCOL.md``. We tolerate ``config`` arriving before or after, and a
        missing ``config`` (defaults apply).
        """
        try:
            first = await self._receive_json_with_timeout(timeout=15.0)
        except (asyncio.TimeoutError, WebSocketDisconnect):
            await self._send_error("handshake_timeout", "No hello received.")
            return False

        if first is None or first.get("type") != "hello":
            await self._send_error(
                "expected_hello", "First message must be type 'hello'."
            )
            return False

        self._hello = first
        session_info = first.get("session") or {}
        self.resume_of = session_info.get("resumeId")
        if not await self._resolve_mode(first):
            return False

        # Opportunistically consume a following ``config`` if present soon.
        with contextlib.suppress(asyncio.TimeoutError, WebSocketDisconnect):
            second = await self._receive_json_with_timeout(timeout=0.5)
            if second is not None and second.get("type") not in (None, "config"):
                # Not a config; stash nothing — it will be re-handled? We cannot
                # push back, so handle known early types inline.
                await self._dispatch_control(second)
        return True

    async def _resolve_mode(self, hello: dict[str, Any]) -> bool:
        """Read ``hello.mode`` and validate its ``translate`` block.

        Returns ``False`` (after sending a fatal error) only for a *named* mode
        this build does not have. Anything else falls back to ``"agent"``: an
        absent or empty ``mode`` is what every client written before this
        feature sends, and those must keep working untouched.

        The target language is validated against
        ``settings.translate_allowed_target_langs`` rather than forwarded, because
        it is client-supplied and lands verbatim in the upstream setup message.
        """
        mode = hello.get("mode")
        if mode in (None, "", "agent"):
            self._mode = "agent"
            self._manual_vad = self._wants_manual_vad(hello)
            return True
        if mode != "translate":
            # A mode we don't implement is a bug in the client, not something
            # to silently downgrade — downgrading would translate nothing and
            # answer questions instead, which is worse than failing loudly.
            await self._send_error(
                "unsupported_mode",
                f"Session mode {mode!r} is not supported.",
                fatal=True,
            )
            return False

        block = hello.get("translate")
        block = block if isinstance(block, dict) else {}
        target = str(block.get("targetLanguage") or "en").strip().lower()
        allowed = [
            str(c).strip().lower()
            for c in self._settings.translate_allowed_target_langs
        ]
        if target not in allowed:
            target = "en"
            logger.warning(
                "translate.target_not_allowed",
                session_id=self.session_id,
                requested=str(block.get("targetLanguage"))[:20],
            )
        self._mode = "translate"
        self._translate = {
            "target_language": target,
            "echo_target_language": bool(block.get("echoTargetLanguage", False)),
            # The phone can say the translation itself, with the voice Android
            # already ships. Then we send the words and no audio at all.
            #
            # This is not a small saving: of the ~$0.031 a minute of translation
            # costs, ~$0.025 is the spoken audio. Producing it on the device
            # takes the bill to about a sixth, and removes the second and a half
            # the cloud voice spends before its first sound.
            "speak_on_device": bool(block.get("speakOnDevice", False)),
        }
        logger.info(
            "translate.mode",
            session_id=self.session_id,
            target=target,
            echo=self._translate["echo_target_language"],
            speak_on_device=self._translate["speak_on_device"],
        )
        return True

    async def _receive_json_with_timeout(
        self, timeout: float
    ) -> dict[str, Any] | None:
        """Receive one text frame as JSON within ``timeout`` seconds.

        Returns ``None`` for non-text frames received during the handshake.
        """
        message = await asyncio.wait_for(self._ws.receive(), timeout=timeout)
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        text = message.get("text")
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _frame_wait_for_kind(self, device_kind: str | None) -> float:
        """Frame-wait budget for a camera ``kind``.

        The kind is a combo when the channels come from different devices
        (e.g. ``"phone+glasses"`` = phone mic + glasses camera), so match by
        substring: any glasses involvement means the camera may be the
        photo-trigger one, which needs the longer budget.
        """
        if isinstance(device_kind, str) and "glasses" in device_kind:
            return self._settings.glasses_frame_wait_seconds
        return self._settings.frame_wait_seconds

    # -- Read pump (client -> gateway) ---------------------------------------

    async def _read_pump(self) -> None:
        """Read frames from the client and route them until disconnect."""
        while True:
            message = await self._ws.receive()
            mtype = message.get("type")
            if mtype == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))

            if message.get("bytes") is not None:
                await self._handle_binary(message["bytes"])
            elif message.get("text") is not None:
                await self._handle_text(message["text"])

    async def _audio_sender(self) -> None:
        """Forward queued microphone frames without blocking the read pump."""
        while True:
            item = await self._audio_queue.get()
            try:
                if item is None:
                    return
                pcm, ts_ms = item
                await self._gateway.send_audio(pcm, ts_ms=ts_ms)
            finally:
                self._audio_queue.task_done()

    async def _audio_dump_writer(self) -> None:
        """Write diagnostic WAV frames off the live event loop.

        ``wave.writeframes`` is synchronous filesystem I/O.  It is safe in a
        worker thread, but it must never run from ``_handle_binary`` where it
        can make the next spoken word wait behind a slow disk.
        """
        queue = self._audio_dump_queue
        assert queue is not None
        while True:
            pcm = await queue.get()
            try:
                if pcm is None:
                    return
                await asyncio.to_thread(self._audio_dump.write, pcm)
            finally:
                queue.task_done()

    async def _queue_audio(self, pcm: bytes, ts_ms: int | None) -> None:
        """Hand one mic frame to the background sender without stale buildup."""
        # Direct calls to _handle_binary in focused tests do not start run(),
        # so retain the old immediate behaviour for that narrow lifecycle.
        if self._audio_sender_task is None:
            await self._gateway.send_audio(pcm, ts_ms=ts_ms)
            return
        try:
            self._audio_queue.put_nowait((pcm, ts_ms))
        except asyncio.QueueFull:
            # The provider stopped taking audio for a moment (a stalled
            # model-side detector, a slow link). This used to END the session
            # with a fatal error — twice on 2026-09-13 mid-conversation
            # (sessions 834742, 65db89), each time 18 s after the model had
            # gone quiet on glasses audio. Losing 40 ms of stale mic audio
            # is nothing; losing the session is everything. Drop the OLDEST
            # frame (the newest is the one still worth hearing), count it,
            # and say so once in a while.
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                pass
            self._audio_queue.put_nowait((pcm, ts_ms))
            self._audio_dropped += 1
            now = time.monotonic()
            if now - self._audio_drop_logged_at >= 5.0:
                self._audio_drop_logged_at = now
                logger.warning(
                    "audio.forward_queue_full",
                    session_id=self.session_id,
                    dropped_total=self._audio_dropped,
                    queued=self._audio_queue.qsize(),
                )

    def _queue_audio_dump(self, pcm: bytes) -> None:
        """Best-effort diagnostic copy; it must never delay live audio."""
        queue = self._audio_dump_queue
        if queue is None:
            # Same direct-test compatibility as _queue_audio.  In a running
            # session an enabled dump always has its dedicated writer.
            self._audio_dump.write(pcm)
            return
        try:
            queue.put_nowait(pcm)
        except asyncio.QueueFull:
            logger.warning(
                "audio_dump.queue_full",
                session_id=self.session_id,
                queued=queue.qsize(),
            )

    async def _handle_binary(self, data: bytes) -> None:
        """Decode a binary media frame and forward it to the gateway."""
        try:
            tag, ts, payload = decode_frame(data)
        except ValueError as exc:
            logger.warning("frame.decode_error", error=str(exc))
            return

        if tag == FrameTag.INPUT_AUDIO:
            metrics.FRAMES_IN.labels(kind="audio").inc()
            metrics.AUDIO_BYTES_IN.inc(len(payload))
            self._queue_audio_dump(payload)
            if self._mode == "agent" and getattr(
                self._settings, "refine_user_transcripts", False
            ):
                self._turn_pcm += payload
                if seconds_of(bytes(self._turn_pcm)) > 60:
                    # A quiet room streams nothing (the gate holds silence
                    # back), so this only grows while someone talks; still,
                    # bound it — the last 30 s are what is on screen.
                    self._turn_pcm = bytearray(clip_to_budget(bytes(self._turn_pcm)))
            now_audio = time.monotonic()
            # Input-lag evidence: mic audio streams every 20-100 ms, so a gap
            # followed by a burst means the CLIENT held the audio back (mic
            # gate learning the room, echo-guard tail, capture stall) — the
            # user's "Farry heard me late". One log line per resume makes the
            # exact hold visible instead of argued about.
            if (
                self._last_audio_frame_at > 0.0
                and now_audio - self._last_audio_frame_at > 1.5
            ):
                logger.info(
                    "audio.resumed",
                    session_id=self.session_id,
                    gap_ms=int((now_audio - self._last_audio_frame_at) * 1000),
                )
            self._last_audio_frame_at = now_audio
            # NOT an idle reset. The mic streams every 20-100 ms whether or not
            # anyone is talking, so counting frames as activity meant the idle
            # cap could never fire while the app was open — device-seen
            # 2026-08-28: a forgotten session sat 40 min in silence, billing
            # input audio, until the 30-min hard cap (which counts from
            # session start) was the only thing that ever ended anything.
            # Agent-mode activity is now the user actually being HEARD (the
            # transcript path) or typing; a translator has no turns, so for
            # translate the streamed speech itself stays the signal of life.
            if self._mode == "translate":
                self._last_activity = now_audio
            # Translate audio is billed to its OWN meter, never to
            # `voice_seconds`: that is the assistant's allowance, and half an
            # hour of translating a meeting would empty it — leaving someone
            # unable to talk to Farry because they listened to a talk.
            if self._mode == "translate":
                if not await self._meter_translate(len(payload)):
                    return  # over today's cap — the session has been ended
            elif not await self._meter_voice(len(payload)):
                return  # over today's cap — _meter_voice ended the session
            await self._queue_audio(payload, ts)
            # The unheard-audio cap reasons about the model's AUTOMATIC
            # detector; with manual markers the turn window is ours.
            if self._mode == "agent" and not self._manual_vad:
                await self._note_unheard_audio(now_audio)
        elif tag == FrameTag.INPUT_VIDEO:
            metrics.FRAMES_IN.labels(kind="video").inc()
            self._frames_in_video += 1
            now = time.monotonic()
            # A translate session has no camera and no tool that could ask for
            # one. Dropping the frame here — rather than trusting the cost gate
            # below to happen to say no — is what makes "no vision in translate
            # mode" true instead of merely likely.
            if self._mode == "translate":
                return
            # Always cache the latest frame (+ arrival time): identify_image and
            # the typed-turn attach read from here, and it lets us reject a
            # stale frame from before the camera was lowered/turned off.
            if self._orchestrator is not None:
                self._orchestrator.last_frame = payload
                self._orchestrator.last_frame_at = now
            # Cost gate: only a fraction of the ~1 fps stream reaches the model.
            # Every frame is re-billed on every LATER turn, so streaming them all
            # is the biggest Live-API cost driver.
            #
            # EXCEPTION — a frame a tool is waiting for (capture_photo) always
            # goes through: its result makes the model describe what it sees, so
            # the frame must already be in the model's realtime-input queue or it
            # answers blind (device-proven 2026-07-11: described a "grey gradient"
            # while the glasses had captured a clear room photo).
            awaited = (
                self._orchestrator is not None
                and self._orchestrator.is_awaiting_frame()
            )
            if awaited or self._should_forward_frame(now):
                await self._forward_frame(
                    payload,
                    ts_ms=ts,
                    reason="awaited" if awaited else self._settings.vision_frame_mode,
                )
            if self._orchestrator is not None:
                # Wake any tool (capture_photo) waiting for a fresh frame — now
                # that the frame is on its way to the model.
                self._orchestrator.notify_new_frame()
        else:
            metrics.FRAMES_IN.labels(kind="unknown").inc()
            logger.warning("frame.unknown_tag", tag=tag)

    async def _handle_text(self, raw: str) -> None:
        """Parse a JSON control message and dispatch it."""
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_error("bad_json", "Could not parse JSON message.")
            return
        if not isinstance(message, dict):
            return
        await self._dispatch_control(message)

    async def _dispatch_control(self, message: dict[str, Any]) -> None:
        """Handle a client control message by ``type`` (``PROTOCOL.md`` §3)."""
        mtype = message.get("type")
        # Automatic messages must not count as activity, or the idle cap never
        # fires (the second half of the never-idles bug; see the INPUT_AUDIO
        # note). Pings arrive every 5 s and location_update every 5 min from
        # any OPEN app — neither means a human is there. Substantive control
        # messages (typed text, audio_start, config, device events) count.
        if mtype not in ("ping", "location_update", "mic_dropped", "gate_missed"):
            self._last_activity = time.monotonic()
        if mtype == "text":
            text = (message.get("text") or "").strip()
            if text and self._voice_capped:
                # The talk budget is spent: typing is not a way around it.
                # Same message, same code — the app shows the cap notice and
                # the Upgrade overlay.
                await self._refuse_over_quota(self._quota_message())
                return
            if text:
                # A typed turn never produces user-transcript deltas, so
                # anchor the turn clock here — response_ms then measures
                # "send tapped → first reply audio", same meaning as voice.
                if self._mode == "agent":
                    now = time.monotonic()
                    if self._t_user_first == 0.0:
                        self._t_user_first = now
                        if self._orchestrator is not None:
                            self._orchestrator.user_turns_heard += 1
                    self._t_user_last = now
                    if self._orchestrator is not None:
                        self._orchestrator.note_user_turn()
                await self._send_state("thinking")
                # A typed turn has no audio VAD, so give the model the current
                # camera view for "what is this?"-style questions even when
                # continuous streaming is gated off.
                await self._attach_frame_if_fresh()
                await self._gateway.send_text(text)
                await repo_safe_transcript(self.session_id, "user", text)
        elif mtype == "audio_start":
            # Mic (un)muted — automatic VAD on the provider handles turn-taking.
            await self._send_state("listening")
        elif mtype == "audio_stop":
            # The mic closed. If a manual activity window is still open the
            # client could not close it (its gate never saw the silence), so
            # close it here — otherwise the model waits forever.
            if self._manual_vad and self._activity_open:
                await self._handle_speech_marker(False)
            await self._send_state("idle")
        elif mtype in ("speech_start", "speech_end"):
            await self._handle_speech_marker(mtype == "speech_start")
        elif mtype == "interrupt":
            await self._handle_interrupt()
        elif mtype == "ping":
            await self._send_json({"type": "pong", "t": message.get("t")})
        elif mtype == "config":
            # Wire formats are fixed; nothing to negotiate. Acknowledge silently.
            logger.info("config.received", session_id=self.session_id)
        elif mtype == "location_update":
            # The device pushed a fresh GPS fix (+ reverse-geocoded address);
            # cache it on the orchestrator so get_location can read it.
            loc = message.get("location")
            if isinstance(loc, dict) and self._orchestrator is not None:
                self._orchestrator.location = loc
                logger.info("location.updated", session_id=self.session_id)
        elif mtype == "mic_dropped":
            # A measurement from the phone: speech-like audio it threw away
            # during one mute window. `tail_ms` is the part with the speaker
            # already silent — the user talking into a deaf app. Logged so
            # the mute tail can be sized on numbers (see live_controller).
            logger.info(
                "mic.dropped",
                session_id=self.session_id,
                speech_ms=int(message.get("speechMs") or 0),
                tail_ms=int(message.get("tailMs") or 0),
                window_ms=int(message.get("windowMs") or 0),
                chunks=int(message.get("chunks") or 0),
            )
        elif mtype == "gate_missed":
            # The phone's mic gate held back a stretch of speech-like audio.
            # Peak vs bar says whether the voice fell short and by how much
            # — the number to tune the glasses profile on (2026-09-15).
            logger.info(
                "gate.missed",
                session_id=self.session_id,
                mic=str(message.get("mic") or "")[:16],
                peak=int(message.get("peakRms") or 0),
                bar=int(message.get("bar") or 0),
                floor=int(message.get("floor") or 0),
                loud_ms=int(message.get("loudMs") or 0),
                half_ms=int(message.get("halfMs") or 0),
                mean=int(message.get("meanRms") or 0),
                route=str(message.get("route") or "")[:16],
                sco_up=message.get("scoUp"),
            )
        elif mtype == "call_state":
            # A phone call took the microphone, or gave it back. The model
            # cannot see this, so it kept reporting a finished call as still
            # ringing (device-observed 2026-09-06). A silent note puts the fact
            # in its context without making it announce anything.
            in_call = bool(message.get("inCall"))
            note_fn = getattr(self._gateway, "send_silent_note", None)
            if callable(note_fn):
                await note_fn(
                    "(A phone call is in progress. The user cannot talk to you "
                    "until it ends.)"
                    if in_call
                    else "(The phone call has ended. You are no longer calling "
                    "anyone \u2014 do not say a call is in progress.)"
                )
            logger.info(
                "call_state.updated", session_id=self.session_id, in_call=in_call
            )
        elif mtype == "device_update":
            # The client switched its active capture device mid-session (e.g.
            # glasses connected by voice AFTER hello and became the camera).
            # Re-pick the frame-wait budget from the NEW camera kind, so a
            # photo-trigger glasses capture gets the long budget it needs —
            # without this the session kept the hello-time "phone" budget and
            # cut off every glasses photo (device-proven 2026-07-11).
            audio_kind = message.get("audioKind")
            if isinstance(audio_kind, str):
                await self._audio_kind_changed(audio_kind)
            new_kind = message.get("videoKind")
            if self._orchestrator is not None and isinstance(new_kind, str):
                budget = self._frame_wait_for_kind(new_kind)
                self._orchestrator.set_frame_wait_seconds(budget)
                # Keep the gateway's frame-freshness window in step with the
                # new camera (glasses connected mid-session → widen it).
                if self._gateway is not None:
                    self._gateway.set_camera_kind(new_kind)
                logger.info(
                    "device.updated",
                    session_id=self.session_id,
                    video_kind=new_kind,
                    frame_wait_seconds=budget,
                )
        elif mtype == "capture_failed":
            # The device could not deliver the photo a vision tool is waiting
            # for (glasses not connected / camera busy / transfer stalled...).
            # Wake the waiting tool NOW with the precise reason instead of
            # letting it run out the full frame timeout.
            reason = message.get("reason")
            if self._orchestrator is not None:
                self._orchestrator.notify_capture_failed(
                    reason if isinstance(reason, str) and reason else "unknown"
                )
                logger.info(
                    "capture.failed",
                    session_id=self.session_id,
                    reason=reason,
                )
        elif mtype == "resolve_contact_result":
            # The device finished resolving a contact name locally (privacy-
            # preserving). Hand the masked result back to the awaiting tool.
            req_id = message.get("requestId")
            if isinstance(req_id, str) and self._orchestrator is not None:
                self._orchestrator.resolve_pending(req_id, message)
        elif mtype == "tool_permission":
            # Permission gating is optional; tools are not gated by default.
            logger.info(
                "tool_permission.received",
                session_id=self.session_id,
                granted=message.get("granted"),
            )
        elif mtype == "hello":
            # Duplicate hello after handshake — ignore.
            pass
        else:
            logger.warning("control.unknown_type", type=mtype)

    async def _session_watchdog(self) -> None:
        """End runaway or idle sessions to bound cost.

        A live session re-bills its whole history every turn, so one left open
        (or forgotten in a pocket) keeps burning tokens. On a limit we tell the
        client (``session_expired``) and return; run()'s FIRST_COMPLETED wait
        then tears the session down and closes the socket, and the app
        reconnects fresh with a cheap, empty context. Both caps are generous and
        configurable; 0 disables one.
        """
        # A translator is not a conversation. `max_session_seconds` (30 min) is
        # sized for someone talking to an assistant; a talk, a lecture or a
        # meeting runs longer, and being cut off mid-sentence is the failure
        # this cap was never meant to cause.
        max_s = (
            self._settings.translate_max_session_seconds
            if self._mode == "translate"
            else self._settings.max_session_seconds
        )
        idle_s = self._settings.idle_disconnect_seconds
        if max_s <= 0 and idle_s <= 0:
            return  # no caps → let the read/event pumps own the lifetime
        while not self._closing:
            await asyncio.sleep(5)
            now = time.monotonic()
            if max_s > 0 and now - self._session_started >= max_s:
                await self._expire_session("max_duration")
                return
            if idle_s > 0 and now - self._last_activity >= idle_s:
                await self._expire_session("idle")
                return

    async def _expire_session(self, reason: str) -> None:
        """Tell the client why the session is ending (best-effort)."""
        logger.info(
            "session.expired", session_id=self.session_id, reason=reason,
            duration_s=round(time.monotonic() - self._session_started),
        )
        with contextlib.suppress(Exception):
            await self._send_json({"type": "session_expired", "reason": reason})

    def _should_forward_frame(self, now: float) -> bool:
        """Whether the cost gate lets this video frame through to the model."""
        # Adapters that attach the frame themselves on each real turn need no
        # heartbeat — see AIGateway.attaches_frame_per_turn.
        if self._gateway is not None and getattr(
            self._gateway, "attaches_frame_per_turn", False
        ):
            return False
        mode = self._settings.vision_frame_mode
        if mode == "off":
            return False
        interval = (
            self._settings.vision_frame_min_interval_s
            if mode == "continuous"
            else self._settings.vision_frame_heartbeat_s
        )
        return now - self._last_video_sent >= interval

    async def _forward_frame(
        self, jpeg: bytes, *, ts_ms: int | None = None, reason: str
    ) -> None:
        """Send ONE video frame to the model, counting and logging it.

        The single choke-point for every frame that reaches the model, so the
        log line below is an exact, auditable count of billed camera frames
        (``sent`` vs ``received`` shows how much the cost gate saved).
        """
        self._last_video_sent = time.monotonic()
        self._frames_sent_video += 1
        metrics.FRAMES_SENT_TO_MODEL.inc()
        logger.info(
            "vision.frame_forwarded",
            session_id=self.session_id,
            reason=reason,  # on_turn | continuous | awaited | typed_turn
            sent=self._frames_sent_video,
            received=self._frames_in_video,
            bytes=len(jpeg),
        )
        await self._gateway.send_video(jpeg, ts_ms=ts_ms)
        # A frame a vision tool (or a typed turn) is waiting on must ALSO go in
        # as conversation content: the realtime stream alone is not reliably
        # incorporated while the model sits suspended mid-turn — it answered
        # "I can't see anything" over a delivered picture (device-proven
        # 2026-08-27). Streaming-gate frames stay realtime-only; doubling every
        # continuous frame into the transcript would balloon the context.
        if reason in ("awaited", "typed_turn", "on_turn"):
            # getattr: a gateway without the hook (a provider that predates
            # it, or a test double) keeps its old realtime-only behavior.
            attach = getattr(self._gateway, "attach_image", None)
            if attach is not None:
                await attach(jpeg)

    async def _attach_frame_if_fresh(self) -> None:
        """Forward the latest cached camera frame once, if it is recent.

        Used at the start of a typed turn so visual questions still work when
        continuous frame streaming is gated off (``vision_frame_mode`` !=
        ``continuous``). Stale frames (camera lowered/off) are skipped.
        """
        if self._gateway is None or self._orchestrator is None:
            return
        if self._settings.vision_on_demand_only:
            # Same rule as spoken turns: sight arrives via identify_image /
            # capture_photo when the user asks about it, never by default.
            return
        frame = self._orchestrator.last_frame
        if frame is None:
            return
        arrived = self._orchestrator.last_frame_at or 0.0
        if time.monotonic() - arrived > 10.0:  # stale — camera likely off
            return
        await self._forward_frame(frame, reason="typed_turn")

    async def _handle_interrupt(self) -> None:
        """Barge-in: cancel TTS/generation and reset state to listening."""
        logger.info("interrupt", session_id=self.session_id)
        if self._mode == "agent":
            self._log_turn_timing(outcome="interrupted")
        await self._gateway.interrupt()
        await self._send_state("listening")

    async def _save_user_final(self, live_text: str) -> None:
        """Store the Live transcriber's final user text — unless a better
        reading of the same turn already landed, in which case store THAT."""
        turn = self._turn_index
        refined = self._turn_refined_text.pop(turn, None)
        row_id = await repo_safe_transcript(
            self.session_id, "user", refined or live_text
        )
        if refined is None and row_id is not None:
            # The second reading may still be on its way; leave it the row.
            self._turn_rows[turn] = row_id

    def _start_refining_turn(self) -> None:
        """Hand this turn's audio to a second reader, off the critical path."""
        pcm = bytes(self._turn_pcm)
        self._turn_pcm = bytearray()
        if not getattr(self._settings, "refine_user_transcripts", False):
            return
        if seconds_of(pcm) < MIN_AUDIO_SECONDS:
            return
        turn = self._turn_index
        self._refine_task = asyncio.create_task(self._refine_and_send(pcm, turn))

    async def _refine_and_send(self, pcm: bytes, turn: int) -> None:
        started = time.monotonic()
        text = await refine_transcript(
            pcm,
            api_key=self._settings.gemini_api_key,
            model=self._settings.refine_transcript_model,
            timeout_s=self._settings.refine_transcript_timeout_s,
        )
        if not text or self._closing:
            return
        logger.info(
            "transcript.refined",
            session_id=self.session_id,
            turn=turn,
            audio_s=round(seconds_of(pcm), 1),
            took_ms=int((time.monotonic() - started) * 1000),
        )
        await self._send_json(
            {
                "type": "transcript",
                "role": "user",
                "text": text,
                "final": True,
                # A correction to the bubble already on screen, not a new one.
                "refined": True,
            }
        )
        row_id = self._turn_rows.pop(turn, None)
        if row_id is not None:
            # The Live final got there first: correct its row in place.
            await repo_safe_update_transcript(row_id, text)
        else:
            # We are first: the Live final, when it comes, will save this.
            self._turn_refined_text[turn] = text

    def _log_turn_timing(self, *, outcome: str) -> None:
        """One summary line + metrics for the turn that just ended.

        Emits ``turn.timing`` with the three spans an investigation needs:
        ``listen_ms`` (how long the user was heard), ``response_ms`` (last
        user words → first reply-audio byte — the silence the user feels),
        and ``speak_ms`` (how long the reply ran). A turn with no observable
        anchors — e.g. a TURN_COMPLETE caused by an internal context note —
        is skipped so the averages aren't polluted with zeros.
        """
        now = time.monotonic()
        if self._t_user_last == 0.0 and self._t_reply_started == 0.0:
            return
        listen_ms = (
            int((self._t_user_last - self._t_user_first) * 1000)
            if self._t_user_first > 0.0
            else None
        )
        response_ms = (
            int((self._t_first_audio_sent - self._t_user_last) * 1000)
            if self._t_first_audio_sent > 0.0 and self._t_user_last > 0.0
            else None
        )
        speak_ms = (
            int((now - self._t_reply_started) * 1000)
            if self._t_reply_started > 0.0
            else None
        )
        metrics.TURNS.labels(outcome=outcome).inc()
        if listen_ms is not None:
            metrics.TURN_LISTEN.observe(listen_ms / 1000)
        if speak_ms is not None:
            metrics.TURN_SPEAK.observe(speak_ms / 1000)
        logger.info(
            "turn.timing",
            session_id=self.session_id,
            turn=self._turn_index,
            outcome=outcome,
            listen_ms=listen_ms,
            response_ms=response_ms,
            speak_ms=speak_ms,
            tools=self._turn_tools,
        )
        self._turn_index += 1
        self._t_user_first = 0.0
        self._t_user_last = 0.0
        self._t_reply_started = 0.0
        self._t_first_audio_sent = 0.0
        self._turn_tools = 0
        self._unheard_audio_since = 0.0

    async def _silence_filler(self) -> None:
        """Poll for a burst that just ended and fill the quiet after it."""
        try:
            while not self._closing:
                await asyncio.sleep(0.1)
                await self._maybe_fill_silence(time.monotonic())
        except asyncio.CancelledError:  # pragma: no cover - teardown
            raise
        except Exception as exc:  # noqa: BLE001 - a filler must never end a session
            logger.warning("audio.filler_failed", session_id=self.session_id, error=repr(exc))

    async def _maybe_fill_silence(self, now: float) -> bool:
        """After the client's gate closes, send the silence it withheld.

        The provider's end-of-speech decision needs quiet audio AFTER the
        words; the gate stops streaming instead, so the decision waited for
        the next burst (device-seen 2026-09-11: four short questions, 27 s
        unheard). Once per burst: when no mic frame has arrived for
        ``vad_silence_filler_after_ms``, ``vad_silence_filler_seconds`` of
        zero PCM go to the provider in 100 ms pieces, stopping the moment a
        real frame arrives. Returns True when a fill was sent.
        """
        seconds = float(getattr(self._settings, "vad_silence_filler_seconds", 0.0) or 0.0)
        if seconds <= 0.0 or self._mode != "agent" or self._manual_vad:
            return False
        last = self._last_audio_frame_at
        if last <= 0.0 or last == self._filler_filled_for:
            return False
        after = int(getattr(self._settings, "vad_silence_filler_after_ms", 300) or 300)
        if now - last < after / 1000.0:
            return False
        self._filler_filled_for = last
        chunk = bytes(16_000 * 2 // 10)  # 100 ms of 16 kHz PCM16 silence
        pieces = max(1, int(round(seconds * 10)))
        sent = 0
        for _ in range(pieces):
            if self._closing or self._last_audio_frame_at != last:
                break  # a real frame arrived: the gate reopened, stop filling
            await self._queue_audio(chunk, None)
            sent += 1
            await asyncio.sleep(0.1)
        logger.info(
            "audio.silence_filled",
            session_id=self.session_id,
            ms=sent * 100,
            gap_ms=int((now - last) * 1000),
        )
        return sent > 0

    async def _turn_watch(self) -> None:
        """Poll for a pause after unheard speech and nudge at once."""
        try:
            while not self._closing:
                await asyncio.sleep(0.1)
                await self._maybe_quiet_nudge(time.monotonic())
        except asyncio.CancelledError:  # pragma: no cover - teardown
            raise
        except Exception as exc:  # noqa: BLE001 - a watcher must never end a session
            logger.warning("turn.watch_failed", session_id=self.session_id, error=repr(exc))

    async def _maybe_quiet_nudge(self, now: float) -> bool:
        """The user stopped and nobody was heard: close the stream now.

        Complements ``_note_unheard_audio`` (which fires while audio is still
        flowing, as a cap). This one fires when the gate has gone quiet for
        ``stuck_turn_quiet_nudge_seconds`` after audio that opened no turn -
        the user finished a sentence and the provider is sitting on it. Once
        per pause. Returns True when a nudge was sent.
        """
        quiet = float(getattr(self._settings, "stuck_turn_quiet_nudge_seconds", 0.0) or 0.0)
        if quiet <= 0.0 or self._mode != "agent" or self._manual_vad:
            return False
        if self._unheard_audio_since == 0.0:
            return False  # nothing unheard is pending
        if self._t_user_first > 0.0 or self._t_reply_started > 0.0:
            return False
        last = self._last_audio_frame_at
        if last <= 0.0 or last == self._quiet_nudged_for:
            return False
        if now - last < quiet:
            return False
        self._quiet_nudged_for = last
        self._quiet_nudges += 1
        give_up = int(getattr(self._settings, "stuck_reconnect_after_nudges", 0) or 0)
        if give_up > 0 and self._quiet_nudges >= give_up:
            # Several separate utterances, each followed by a pause, none
            # heard: the connection is deaf. Drop the socket WITHOUT a
            # session_expired notice so the app reconnects by itself (the
            # resume handle keeps the conversation). Counted on QUIET nudges
            # only: music keeps the gate open and never gets here.
            logger.warning(
                "turn.stuck_reconnect",
                session_id=self.session_id,
                turn=self._turn_index,
                nudges=self._quiet_nudges,
            )
            self._quiet_nudges = 0
            self._cap_nudges = 0
            closer = getattr(self, "_close_for_reconnect", None)
            if callable(closer):
                await closer()
            return True
        fn = getattr(self._gateway, "send_audio_stream_end", None)
        if not callable(fn):
            return False
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("turn.nudge_failed", session_id=self.session_id, error=repr(exc))
            return False
        # The cap window restarts too, so the two triggers never double up.
        self._unheard_audio_since = now
        logger.info(
            "turn.nudge",
            session_id=self.session_id,
            turn=self._turn_index,
            quiet_s=round(now - last, 1),
            unheard_s=None,
        )
        return True

    async def _note_unheard_audio(self, now: float) -> None:
        """Mic audio arrived: is anyone being heard?

        Outdoors on 2026-09-09 the provider's detector went 28 s and then 67 s
        with audio flowing and no turn opened, then delivered the lot as one
        33 s turn - to the user, a minute of "she can't hear me". This counts
        audio that arrives while no user speech has been heard in the current
        turn and the assistant is not replying; past
        ``stuck_turn_nudge_seconds`` it closes the provider's audio stream so
        the detector must finalise, and the window restarts (a second nudge
        follows if that changed nothing). The gate holds silence back, so
        only speech-like audio can advance this; a quiet room never nudges.
        """
        limit = float(getattr(self._settings, "stuck_turn_nudge_seconds", 0.0) or 0.0)
        if limit <= 0.0:
            return
        if self._t_user_first > 0.0 or self._t_reply_started > 0.0:
            self._unheard_audio_since = 0.0
            self._cap_nudges = 0
            self._quiet_nudges = 0
            return
        if self._unheard_audio_since == 0.0:
            self._unheard_audio_since = now
            return
        if now - self._unheard_audio_since < limit:
            return
        self._unheard_audio_since = now
        self._cap_nudges += 1
        fn = getattr(self._gateway, "send_audio_stream_end", None)
        if not callable(fn):
            return
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001 - a nudge must never end a session
            logger.warning("turn.nudge_failed", session_id=self.session_id, error=repr(exc))
            return
        logger.info(
            "turn.nudge",
            session_id=self.session_id,
            turn=self._turn_index,
            unheard_s=round(limit, 1),
        )

    async def supersede(self) -> None:
        """A newer connection from the same account is taking over.

        Tell this client WHY (``session_expired`` / ``superseded``, which the
        app treats as a deliberate end and does not auto-reconnect) and close
        the socket so :meth:`run` unwinds and the gateway is released. One
        account, one live session: until 2026-09-13 every reconnect, retry or
        second device simply stacked another session on top of the running
        one, and each of them streamed the mic to the model and answered.
        """
        if self._closing:
            return
        await self._expire_session("superseded")
        ws = getattr(self, "_ws", None)
        if ws is None:
            return
        try:
            if ws.application_state == WebSocketState.CONNECTED:
                await ws.close(code=1000)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "session.supersede_close_failed",
                session_id=self.session_id,
                error=repr(exc),
            )

    # -- Manual activity detection (glasses mic) -----------------------------

    def _wants_manual_vad(self, hello: dict[str, Any]) -> bool:
        """Whether this session's MICROPHONE is the glasses.

        ``hello.device.kind`` is the app's active capture pair: ``"glasses"``
        when both mic and camera are the glasses, ``"glasses+phone"`` when the
        mic is the glasses and the camera the phone (audio first). Anything
        else keeps the model's automatic detector.
        """
        if not bool(getattr(self._settings, "manual_vad_for_glasses", False)):
            return False
        device = hello.get("device")
        kind = device.get("kind") if isinstance(device, dict) else None
        if not isinstance(kind, str):
            return False
        return kind.split("+", 1)[0].strip().lower() == "glasses"

    def _apply_vad_mode(self) -> None:
        """Tell a gateway that supports it which detector to run."""
        gw = self._gateway
        if gw is None or not hasattr(gw, "manual_vad"):
            if self._manual_vad:
                logger.info(
                    "vad.manual_unsupported",
                    session_id=self.session_id,
                    provider=getattr(gw, "provider", None),
                )
            self._manual_vad = False
            return
        if getattr(gw, "requires_manual_vad", False):
            # A gateway with no detector of its own (the cascade provider):
            # the app's speech markers ARE its utterances, whatever the mic.
            self._manual_vad = True
        gw.manual_vad = self._manual_vad
        logger.info(
            "vad.mode",
            session_id=self.session_id,
            manual=self._manual_vad,
        )

    async def _handle_speech_marker(self, start: bool) -> None:
        """The client's mic gate opened (``speech_start``) or closed.

        With manual detection these are the model's activityStart /
        activityEnd: the turn opens exactly when speech energy crossed the
        gate's bar and closes when it fell silent, so the model never fires
        on the room and never misses an onset. Under automatic detection they
        are informational only.
        """
        if start:
            # A person is talking: that is activity, whatever the model makes
            # of it (the idle cap otherwise counts only heard turns).
            self._last_activity = time.monotonic()
        if not self._manual_vad or self._gateway is None:
            return
        # Strictly alternating: a second start while a window is open would
        # confuse the model, and an end with nothing open is a no-op.
        if start == self._activity_open:
            return
        try:
            if start:
                await self._gateway.send_activity_start()
            else:
                await self._gateway.send_activity_end()
            self._activity_open = start
        except Exception as exc:  # noqa: BLE001 - a marker must never end a session
            logger.warning(
                "vad.marker_failed",
                session_id=self.session_id,
                start=start,
                error=repr(exc),
            )
            return
        logger.info(
            "vad.activity", session_id=self.session_id, start=start
        )

    async def _audio_kind_changed(self, audio_kind: str) -> None:
        """The app switched microphones mid-session (device_update).

        The detector mode is fixed at connect time, so when the switch means
        a different mode, close the socket the way the stuck-turn path does:
        the app reconnects at once, its hello names the new mic, and the
        resume handle keeps the conversation.
        """
        if self._mode != "agent" or self._gateway is None:
            return
        if not hasattr(self._gateway, "manual_vad"):
            return
        if not bool(getattr(self._settings, "manual_vad_for_glasses", False)):
            return
        wants = audio_kind.strip().lower() == "glasses"
        if wants == self._manual_vad:
            return
        logger.info(
            "vad.mode_change_reconnect",
            session_id=self.session_id,
            audio_kind=audio_kind,
            manual=wants,
        )
        await self._close_for_reconnect()

    async def _close_for_reconnect(self) -> None:
        """Close the client socket so the app reconnects (see stuck_reconnect)."""
        ws = getattr(self, "_ws", None)
        if ws is None:
            return
        try:
            if ws.application_state == WebSocketState.CONNECTED:
                await ws.close(code=1012)  # "service restart": try again
        except Exception as exc:  # noqa: BLE001
            logger.warning("turn.stuck_reconnect_failed", session_id=self.session_id, error=repr(exc))

    # -- Event pump (gateway -> client) --------------------------------------

    async def _event_pump(self) -> None:
        """Translate gateway events into server messages until stream end."""
        async for event in self._gateway.events():
            await self._handle_event(event)

    async def _handle_event(self, event: GatewayEvent) -> None:
        """Map one :class:`GatewayEvent` to a ``PROTOCOL.md`` server message."""
        if event.type == EventType.TRANSCRIPT:
            assert isinstance(event, TranscriptEvent)
            if (
                self._mode == "agent"
                and event.role == "user"
                and not event.final
            ):
                now = time.monotonic()
                # THE idle reset for agent mode: the model is transcribing the
                # user's words right now — real activity, unlike the always-on
                # mic stream or the 5-second heartbeat pings.
                self._last_activity = now
                if self._t_user_first == 0.0:
                    self._t_user_first = now
                    self._cap_nudges = 0
                    if self._orchestrator is not None:
                        self._orchestrator.note_user_turn()
                    self._quiet_nudges = 0
                    logger.info(
                        "turn.hearing",
                        session_id=self.session_id,
                        turn=self._turn_index,
                    )
                self._t_user_last = now
            if (
                event.role != "user"
                and event.final
                and (event.text or "").strip()
                and self._orchestrator is not None
            ):
                self._orchestrator.note_assistant_spoke()
            await self._send_json(
                {
                    "type": "transcript",
                    "role": event.role,
                    "text": event.text,
                    "final": event.final,
                    # Only the translate path fills this in — there the source
                    # language is detected, not configured, so it is the one
                    # thing the client cannot work out for itself. Omitted
                    # otherwise, so nothing on the agent path changes shape.
                    **({"lang": event.lang} if event.lang else {}),
                    # Which sentence this is. The translate path runs several
                    # in flight at once, so the client cannot assume the newest
                    # turn is the one being answered.
                    **(
                        {"utterance": event.utterance}
                        if event.utterance is not None
                        else {}
                    ),
                }
            )
            if event.final and event.text:
                if event.role == "user" and self._mode == "agent":
                    await self._save_user_final(event.text)
                else:
                    await repo_safe_transcript(
                        self.session_id, event.role, event.text
                    )
        elif event.type == EventType.AUDIO_START:
            assert isinstance(event, AudioStartEvent)
            if self._mode == "agent" and self._t_reply_started == 0.0:
                self._t_reply_started = time.monotonic()
                self._start_refining_turn()
            await self._send_state("speaking")
            await self._send_json({"type": "audio_start"})
        elif event.type == EventType.AUDIO_CHUNK:
            assert isinstance(event, AudioChunkEvent)
            if self._mode == "agent" and self._t_first_audio_sent == 0.0:
                now = time.monotonic()
                self._t_first_audio_sent = now
                if self._t_user_last > 0.0:
                    response_s = now - self._t_user_last
                    metrics.TURN_RESPONSE.observe(response_s)
                    logger.info(
                        "turn.first_audio",
                        session_id=self.session_id,
                        turn=self._turn_index,
                        response_ms=int(response_s * 1000),
                    )
            await self._send_audio_frame(event.pcm)
        elif event.type == EventType.AUDIO_END:
            assert isinstance(event, AudioEndEvent)
            await self._send_json({"type": "audio_end"})
        elif event.type == EventType.TOOL_CALL:
            assert isinstance(event, ToolCallEvent)
            self._turn_tools += 1
            await self._spawn_tool_call(event)
        elif event.type == EventType.TURN_COMPLETE:
            assert isinstance(event, TurnCompleteEvent)
            if self._mode == "agent":
                self._log_turn_timing(outcome="complete")
            await self._send_state("listening")
        elif event.type == EventType.ERROR:
            assert isinstance(event, ErrorEvent)
            metrics.AI_ERRORS.labels(provider=self._gateway.provider).inc()
            await self._send_error(event.code, event.message, fatal=event.fatal)

    async def _spawn_tool_call(self, event: ToolCallEvent) -> None:
        """Run a tool call concurrently so the event pump keeps flowing."""
        if self._orchestrator is None:  # pragma: no cover - defensive
            return
        task = asyncio.create_task(
            self._orchestrator.handle_tool_call(event),
            name=f"tool:{event.name}",
        )
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_tasks.discard)

    # -- Outbound helpers -----------------------------------------------------

    async def _send_audio_frame(self, pcm: bytes) -> None:
        """Send an OUTPUT_AUDIO (0x03) binary frame to the client."""
        if not pcm:
            return
        frame = encode_frame(FrameTag.OUTPUT_AUDIO, pcm)
        metrics.AUDIO_BYTES_OUT.inc(len(pcm))
        await self._send_bytes(frame)

    async def _send_state(self, value: str) -> None:
        """Emit a ``state`` server message (and log the transition).

        The log line is the timeline glue: with turn.hearing / turn.first_audio
        / turn.timing it lets a log reader reconstruct exactly when the session
        was listening, thinking, and speaking — no client needed.
        """
        logger.info("state", session_id=self.session_id, value=value)
        await self._send_json({"type": "state", "value": value})

    async def _report_provider_failure(self, exc: BaseException) -> None:
        """The model would not connect: tell the app (fatal, in plain words),
        log the real cause, and mail the operator (rate-limited)."""
        global _OUTAGE_ALERTED_AT
        code, message = classify_provider_failure(exc)
        logger.error(
            "provider.outage",
            session_id=self.session_id,
            code=code,
            provider=getattr(self._gateway, "provider", None),
            error=repr(exc)[:300],
        )
        await self._send_error(code, message, fatal=True)
        to = getattr(self._settings, "first_super_admin_email", None)
        now = time.monotonic()
        # 0.0 means "never": monotonic() counts from boot, so on a machine up
        # for under 30 minutes (a fresh VPS, every CI runner) `now - 0.0` is
        # below the interval and the first outage after boot went unmailed.
        if to and (
            _OUTAGE_ALERTED_AT == 0.0
            or now - _OUTAGE_ALERTED_AT >= _OUTAGE_ALERT_INTERVAL_S
        ):
            _OUTAGE_ALERTED_AT = now
            try:
                from app.modules.auth.notifications import send_outage_alert

                send_outage_alert(
                    to_email=to,
                    subject=f"FarryOn: voice provider down ({code})",
                    text=(
                        "A live session could not connect to the model.\n\n"
                        f"code: {code}\nprovider: "
                        f"{getattr(self._gateway, 'provider', None)}\n"
                        f"error: {repr(exc)[:500]}\n\n"
                        "If this is provider_credits, top up / enable "
                        "auto-reload in Google AI Studio → Billing."
                    ),
                )
            except Exception as mail_exc:  # noqa: BLE001 - alerting is best effort
                logger.warning("provider.outage_alert_failed", error=repr(mail_exc))

    async def _send_error(
        self, code: str, message: str, *, fatal: bool = False
    ) -> None:
        """Emit an ``error`` server message."""
        await self._send_json(
            {
                "type": "error",
                "code": code,
                "message": message,
                "fatal": fatal,
            }
        )

    async def _send_json(self, payload: dict[str, Any]) -> None:
        """Serialize and send a JSON text frame (serialized via a lock)."""
        if self._closing:
            return
        async with self._send_lock:
            if self._ws.application_state != WebSocketState.CONNECTED:
                return
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await self._ws.send_text(json.dumps(payload))

    async def _send_bytes(self, data: bytes) -> None:
        """Send a binary frame (serialized via the same send lock)."""
        if self._closing:
            return
        async with self._send_lock:
            if self._ws.application_state != WebSocketState.CONNECTED:
                return
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await self._ws.send_bytes(data)

    # -- Persistence + teardown ----------------------------------------------

    async def _meter_voice(self, payload_bytes: int) -> bool:
        """Count this audio chunk against today's voice budget.

        Returns False when the speaker is over their plan's daily cap, having
        already told them and closed the session — the caller must not forward
        the audio. Returns True in every other case, including when enforcement
        is off, when the plan is unlimited, and when the DB write fails.

        That last one is deliberate: if we can't record usage we let the audio
        through. Metering exists to protect the operator's bill, and dropping a
        user mid-sentence over a database hiccup costs more than the seconds it
        saves. Under-billing is recoverable; a conversation cut dead is not.
        """
        seconds = payload_bytes / _MIC_BYTES_PER_SECOND
        self._voice_pending_s += seconds

        cap = plan_cap("voice_seconds", self._plan_name)
        enforcing = get_settings().quota_enforcement_enabled

        if enforcing and cap >= 0:
            total = self._voice_used_s + self._voice_pending_s
            if total > cap and not self._voice_capped:
                self._voice_capped = True
                await self._flush_voice_usage()
                logger.info(
                    "quota.voice_exceeded",
                    session_id=self.session_id,
                    user_key=self._usage_key(),
                    used_s=round(total, 1),
                    cap_s=cap,
                )
                await self._refuse_over_quota(self._quota_message())
                return False
            if self._voice_capped:
                return False

        if self._voice_pending_s >= _VOICE_FLUSH_EVERY_S:
            self._schedule_voice_flush()
        return True

    def _quota_message(self) -> str:
        """The one sentence a spent talk budget gets, wherever it is met."""
        settings = get_settings()
        plan = self._plan_name or settings.default_plan
        cap = plan_cap("voice_seconds", plan)
        upsell = "" if plan == "pro" else " Upgrade for more."
        # A sub-minute cap (tests, demos) must not read "0 minutes".
        budget = f"{cap // 60} minutes" if cap >= 60 else f"{cap} seconds"
        window = (
            "your free trial's"
            if settings.usage_window(plan) == "lifetime"
            else "this month's"
        )
        return f"You've used {window} {budget} of voice on the {plan} plan.{upsell}"

    async def _refuse_over_quota(self, message: str) -> None:
        """The one way a spent budget is told: the fatal ``quota_exceeded``
        (the app shows the notice and remembers to offer Upgrade), then
        ``session_expired`` — which every shipped build treats as a
        deliberate end and does NOT auto-reconnect from. Without the second
        message the close that follows looks like a network drop, and a
        capped app reconnects, is refused again, and stacks another notice
        every few seconds instead of showing the Upgrade overlay."""
        await self._send_error("quota_exceeded", message, fatal=True)
        await self._expire_session("quota_exceeded")

    async def _refuse_if_budget_spent(self) -> bool:
        """True — and the session told, fatally — when the talk budget
        loaded by :meth:`_load_voice_usage` is already spent. Nothing is
        connected upstream in that case: no model, no cost."""
        if not get_settings().quota_enforcement_enabled:
            return False
        cap = plan_cap("voice_seconds", self._plan_name)
        if cap < 0 or self._voice_used_s < cap:
            return False
        self._voice_capped = True
        logger.info(
            "quota.refused_at_connect",
            session_id=self.session_id,
            user_key=self._usage_key(),
            used_s=round(self._voice_used_s, 1),
            cap_s=cap,
            plan=self._plan_name,
        )
        await self._refuse_over_quota(self._quota_message())
        return True

    def _usage_key(self) -> str:
        """The daily_usage key — same spelling the tools use, so one person is
        one row rather than two."""
        return user_key_for(self._user_id, self.session_id)

    async def _flush_voice_usage(self) -> None:
        """Write the speech counted since the last flush, and fold it into the
        running total. Best-effort: a failed write must never break the call
        (see :meth:`_meter_voice`), but the seconds are kept pending so the next
        flush still bills them."""
        # A scheduled batch flush and a cap/cleanup flush may arrive together.
        # Serialising them prevents double-billing the same pending seconds.
        lock = getattr(self, "_voice_flush_lock", None)
        if lock is None:  # lightweight test fixtures built via __new__
            lock = asyncio.Lock()
            self._voice_flush_lock = lock
        async with lock:
            if self._voice_pending_s < 1:
                return
            if time.monotonic() < self._voice_flush_retry_at:
                return  # a recent write failed; don't hammer the database
            whole = int(self._voice_pending_s)
            sessionmaker = get_sessionmaker()
            try:
                async with sessionmaker() as db:
                    await repo.bump_daily_usage(
                        db,
                        user_key=self._usage_key(),
                        day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        voice_seconds=whole,
                    )
                    await db.commit()
            except Exception as exc:  # noqa: BLE001 - never break the call over this
                self._voice_flush_retry_at = time.monotonic() + _USAGE_RETRY_AFTER_S
                logger.warning("quota.voice_flush_failed", error=str(exc))
                return
            self._voice_flush_retry_at = 0.0
            self._voice_used_s += whole
            self._voice_pending_s -= whole

    def _schedule_voice_flush(self) -> None:
        """Persist metering in the background; microphone forwarding wins."""
        task = getattr(self, "_voice_flush_task", None)
        if task is None or task.done():
            self._voice_flush_task = asyncio.create_task(
                self._flush_voice_usage(), name="voice_usage_flush"
            )

    async def _meter_translate(self, payload_bytes: int) -> bool:
        """Count this audio chunk against today's translation budget.

        The twin of :meth:`_meter_voice`, and it follows the same rules —
        including the important one: **a DB failure lets the audio through**.
        Metering protects the operator's bill, and cutting someone off
        mid-sentence over a database hiccup costs more than the seconds it saves.

        Two deliberate differences:

        * The cap is always **daily**, on every plan. Voice has a trial/monthly
          split because the free plan's 60 minutes are a lifetime allowance;
          giving translation a second lifetime meter would mean two ways to run
          out and two places to get it wrong.
        * The warning at 80% is sent once. Translation is used in situations —
          a meeting, a talk, a conversation with a stranger — where being cut
          off without notice is worse than the interruption of being told.
        """
        seconds = payload_bytes / _MIC_BYTES_PER_SECOND
        self._translate_pending_s += seconds
        metrics.TRANSLATE_AUDIO_SECONDS.inc(seconds)

        cap = plan_cap("voice_seconds", self._plan_name)
        if not (get_settings().quota_enforcement_enabled and cap >= 0):
            if self._translate_pending_s >= _VOICE_FLUSH_EVERY_S:
                self._schedule_translate_flush()
            return True

        total = self._translate_used_s + self._translate_pending_s
        if total > cap and not self._translate_capped:
            self._translate_capped = True
            await self._flush_translate_usage()
            logger.info(
                "quota.translate_exceeded",
                session_id=self.session_id,
                user_key=self._usage_key(),
                used_s=round(total, 1),
                cap_s=cap,
            )
            plan = self._plan_name or get_settings().default_plan
            upsell = "" if plan == "pro" else " Upgrade for more."
            budget = f"{cap // 60} minutes" if cap >= 60 else f"{cap} seconds"
            await self._refuse_over_quota(
                f"You've used today's {budget} of live translation on the "
                f"{plan} plan.{upsell}"
            )
            return False
        if self._translate_capped:
            return False

        # One heads-up before the wall, not a countdown.
        if not self._translate_warned and total >= cap * 0.8:
            self._translate_warned = True
            left = max(0, int((cap - total) // 60))
            await self._send_error(
                "quota_warning",
                f"About {left} minute{'' if left == 1 else 's'} of translation "
                "left today.",
                fatal=False,
            )

        if self._translate_pending_s >= _VOICE_FLUSH_EVERY_S:
            self._schedule_translate_flush()
        return True

    async def _flush_translate_usage(self) -> None:
        """Write translation seconds counted since the last flush."""
        lock = getattr(self, "_translate_flush_lock", None)
        if lock is None:  # lightweight test fixtures built via __new__
            lock = asyncio.Lock()
            self._translate_flush_lock = lock
        async with lock:
            if self._translate_pending_s < 1:
                return
            if time.monotonic() < self._translate_flush_retry_at:
                return  # a recent write failed; don't hammer the database
            whole = int(self._translate_pending_s)
            try:
                async with get_sessionmaker()() as db:
                    await repo.bump_daily_usage(
                        db,
                        user_key=self._usage_key(),
                        day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        # ONE budget for talking, whoever is being talked to
                        # (2026-09-05): translation runs through the same model at
                        # the same price, so it draws down the same seconds the
                        # assistant does. `translate_seconds` is still written for
                        # the admin view — it is a record of WHAT the minutes went
                        # on, not a second allowance.
                        voice_seconds=whole,
                        translate_seconds=whole,
                    )
                    await db.commit()
            except Exception as exc:  # noqa: BLE001 - never break the call over this
                self._translate_flush_retry_at = (
                    time.monotonic() + _USAGE_RETRY_AFTER_S
                )
                logger.warning("quota.translate_flush_failed", error=str(exc))
                return
            self._translate_flush_retry_at = 0.0
            self._translate_used_s += whole
            self._translate_pending_s -= whole

    def _schedule_translate_flush(self) -> None:
        """Persist translation metering without pausing incoming speech."""
        task = getattr(self, "_translate_flush_task", None)
        if task is None or task.done():
            self._translate_flush_task = asyncio.create_task(
                self._flush_translate_usage(), name="translate_usage_flush"
            )

    async def _talk_used_seconds(self, db: AsyncSession) -> int:
        """Talk seconds already spent in the plan's window.

        One helper for both meters because there is one budget: a trial's
        allowance is a lifetime total, a paid plan's is this calendar month.
        Cold path — called once per session, never per audio frame.
        """
        key = self._usage_key()
        if get_settings().usage_window(self._plan_name) == "lifetime":
            return await repo.lifetime_voice_seconds(db, user_key=key)
        return await repo.usage_this_month(
            db,
            user_key=key,
            month=datetime.now(timezone.utc).strftime("%Y-%m"),
            metric="voice_seconds",
        )

    async def _load_translate_usage(self) -> None:
        """Read today's translation total once, at session start."""
        if not get_settings().quota_enforcement_enabled:
            return
        try:
            async with get_sessionmaker()() as db:
                row = await repo.get_daily_usage(
                    db,
                    user_key=self._usage_key(),
                    day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                )
                from app.modules.billing import service as billing

                self._plan_name = await billing.active_plan_name(
                    db, self._user_id
                )
                # The shared talk budget, over the plan's own window.
                self._translate_used_s = float(
                    await self._talk_used_seconds(db)
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("quota.translate_load_failed", error=str(exc))

    async def _load_voice_usage(self) -> None:
        """Read today's voice total once, at session start.

        Once — not per frame: the cap is a daily budget, and a session that
        started under it may run a little past on its last breath rather than
        pay for a DB read every 20 ms.
        """
        if not get_settings().quota_enforcement_enabled:
            return
        try:
            async with get_sessionmaker()() as db:
                # Read the plan first: it decides whether the budget is this
                # month's or a lifetime trial total.
                # Resolve the caps-bearing plan here, in the same one-shot DB
                # trip: the alternative is a query per audio frame. A signed-in
                # user gets their subscription's plan; anonymous falls back to
                # the default inside active_plan_name.
                from app.modules.billing import service as billing

                self._plan_name = await billing.active_plan_name(db, self._user_id)
                self._voice_used_s = float(await self._talk_used_seconds(db))
        except Exception as exc:  # noqa: BLE001
            logger.warning("quota.voice_load_failed", error=str(exc))

    async def _resolve_owner(self, db: AsyncSession) -> User:
        """Load the signed-in user, or the shared anonymous row if there is none.

        Applies the same rule as the REST side (app/core/account.py): deleted,
        force-logged-out and suspended accounts are all refused. They used not to
        be — only the soft-delete was checked here — so an admin could suspend
        someone and watch them keep talking to Farry until their access token
        aged out, on the operator's model budget.

        Raises :class:`PermissionError` rather than falling back to anonymous:
        that fallback would hand a refused session the shared pile of data.
        """
        if self._authed_user_id is None:
            return await repo.get_or_create_user(db, repo.ANON_EXTERNAL_ID)

        user = await db.get(User, self._authed_user_id)
        rejection = token_rejection(
            user, issued_at=(self._claims or {}).get("iat", 0)
        )
        if rejection is not None:
            raise PermissionError(rejection)
        assert user is not None  # token_rejection returns a code for None
        return user

    async def _persist_session_start(self) -> bool:
        """Resolve the session's owner and record the session start row.

        Returns False when the account may not have a session at all — the
        caller must close. Every other failure returns True: a DB hiccup losing
        an audit row must not take a working conversation down with it.

        The owner is whoever the handshake token named. Everything the agent
        creates from here — notes, tasks, transcripts — is stamped with this id
        (it reaches the tools via ``Orchestrator(user_id=...)``), so this one
        lookup is what makes a user's data theirs.

        Falls back to the shared anonymous row only when nobody is signed in,
        which production forbids at the door (ws/live.py). A token naming a user
        who no longer exists resolves to nothing rather than silently landing in
        the anonymous pile.
        """
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            try:
                user = await self._resolve_owner(db)
                self._user_id = user.id
                client = (self._hello or {}).get("client") or {}
                device = (self._hello or {}).get("device") or {}
                await repo.create_session_row(
                    db,
                    session_id=self.session_id,
                    provider=self._gateway.provider,
                    model=self._gateway.model_label,
                    user_id=user.id,
                    resume_of=self.resume_of,
                    client_platform=client.get("platform"),
                    device_kind=device.get("kind"),
                )
                await db.commit()
            except PermissionError as exc:
                # NOT best-effort. Failing to write an audit row is survivable;
                # "this account may not be here" is not, and swallowing it would
                # leave the session running with no owner — invisible rows and a
                # suspended user still burning model budget.
                await db.rollback()
                logger.warning(
                    "ws.session_rejected",
                    session_id=self.session_id,
                    user_id=self._authed_user_id,
                    reason=str(exc),
                )
                return False
            except Exception as exc:  # noqa: BLE001 - persistence is best-effort
                await db.rollback()
                logger.error("session.persist_failed", error=str(exc))
        return True

    async def _cleanup(self, reason: str) -> None:
        """Cancel tool tasks, close the gateway, mark the session ended."""
        if self._closing:
            return
        self._closing = True
        metrics.WS_DISCONNECTS.labels(reason=reason).inc()
        logger.info(
            "session.closing", session_id=self.session_id, reason=reason
        )
        # Stop background media workers before closing their resources.  Do
        # not wait for an unhealthy provider or a slow diagnostic disk during
        # teardown; session shutdown must remain prompt.
        for task in (
            self._audio_sender_task,
            self._audio_dump_task,
            self._filler_task,
            self._turn_watch_task,
        ):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        await asyncio.to_thread(self._audio_dump.close)
        if self._refine_task is not None and not self._refine_task.done():
            self._refine_task.cancel()
        # Bill the speech since the last flush. Without this every session under
        # _VOICE_FLUSH_EVERY_S would be free, and a user could talk all day in
        # 14-second bursts — the cap would count nothing. The translate meter
        # has exactly the same hole and closes it the same way.
        voice_flush = self._voice_flush_task
        if voice_flush is not None and not voice_flush.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await voice_flush
        await self._flush_voice_usage()
        if self._mode == "translate":
            translate_flush = self._translate_flush_task
            if translate_flush is not None and not translate_flush.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await translate_flush
            await self._flush_translate_usage()
            if self._translate_gauge_held:
                self._translate_gauge_held = False
                metrics.TRANSLATE_ACTIVE.dec()
        # Cost summary: how many camera frames the gate saved this session.
        if self._frames_in_video:
            saved_pct = round(
                100 * (1 - self._frames_sent_video / self._frames_in_video)
            )
            logger.info(
                "vision.frame_summary",
                session_id=self.session_id,
                mode=self._settings.vision_frame_mode,
                received=self._frames_in_video,
                sent_to_model=self._frames_sent_video,
                saved_pct=saved_pct,
            )

        for task in list(self._tool_tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        if self._gateway is not None:
            with contextlib.suppress(Exception):
                await self._gateway.close()

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            with contextlib.suppress(Exception):
                await repo.close_session_row(db, self.session_id)
                await db.commit()

        if self._ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await self._ws.close()


async def repo_safe_transcript(
    session_id: str, role: str, text: str
) -> int | None:
    """Persist a transcript segment, swallowing storage errors.

    Transcripts are convenience history; a DB hiccup must not interrupt the
    live conversation, so failures are logged and dropped. Returns the row
    id so a later, better reading of the same words can correct it in place.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        try:
            row = await repo.add_transcript(
                db, role=role, text=text, session_id=session_id
            )
            await db.commit()
            return row.id
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("transcript.persist_failed", error=str(exc))
            return None


async def repo_safe_update_transcript(transcript_id: int, text: str) -> None:
    """Correct a saved transcript in place; failures are logged and dropped."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        try:
            await repo.update_transcript_text(
                db, transcript_id=transcript_id, text=text
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("transcript.update_failed", error=str(exc))
