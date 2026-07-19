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


class Session:
    """Owns the lifecycle and concurrency for a single live connection."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        gateway_factory: Callable[[str | None, str | None], AIGateway],
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
        self._hello: dict[str, Any] | None = None
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

    # -- Public entrypoint ----------------------------------------------------

    async def run(self) -> None:
        """Handshake, then run both pumps until disconnect; always clean up."""
        reason = "normal"
        try:
            if not await self._handshake():
                reason = "handshake_failed"
                return

            # Now that hello has arrived, build the gateway for the requested
            # provider (or the server default), giving the model the user's
            # local time so reminders resolve in their timezone.
            client_time = (self._hello or {}).get("clientTime")
            prompt = build_system_prompt(
                client_time if isinstance(client_time, str) else None
            )
            self._gateway = self._gateway_factory(
                self._resolve_provider(), prompt
            )

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
                        await self._gateway.connect()
                    except Exception as exc2:  # noqa: BLE001
                        await self._send_error(
                            "provider_unavailable", repr(exc2), fatal=True
                        )
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
                    await self._send_error(
                        "provider_unavailable", repr(exc), fatal=True
                    )
                    reason = "connect_failed"
                    return
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
            await self._load_voice_usage()
            await self._send_json(
                {
                    "type": "ready",
                    "sessionId": self.session_id,
                    "protocolVersion": PROTOCOL_VERSION,
                    "model": self._gateway.model_label,
                }
            )
            await self._send_state("listening")

            web_search = (self._hello or {}).get("webSearch")
            email = (self._hello or {}).get("email")
            emails = (self._hello or {}).get("emails")
            location = (self._hello or {}).get("location")
            # Vision tools wait longer for a frame on photo-trigger glasses
            # than on a streaming phone camera (see Settings for the budgets).
            device = (self._hello or {}).get("device")
            device_kind = device.get("kind") if isinstance(device, dict) else None
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
                web_search=web_search if isinstance(web_search, dict) else None,
                email=email if isinstance(email, dict) else None,
                emails=[e for e in emails if isinstance(e, dict)]
                if isinstance(emails, list)
                else None,
                location=location if isinstance(location, dict) else None,
                frame_wait_seconds=frame_wait_seconds,
            )

            read_task = asyncio.create_task(self._read_pump(), name="read_pump")
            event_task = asyncio.create_task(
                self._event_pump(), name="event_pump"
            )
            watchdog_task = asyncio.create_task(
                self._session_watchdog(), name="watchdog"
            )
            done, pending = await asyncio.wait(
                {read_task, event_task, watchdog_task},
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

        # Opportunistically consume a following ``config`` if present soon.
        with contextlib.suppress(asyncio.TimeoutError, WebSocketDisconnect):
            second = await self._receive_json_with_timeout(timeout=0.5)
            if second is not None and second.get("type") not in (None, "config"):
                # Not a config; stash nothing — it will be re-handled? We cannot
                # push back, so handle known early types inline.
                await self._dispatch_control(second)
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
            self._last_activity = time.monotonic()  # real user turn (idle reset)
            if not await self._meter_voice(len(payload)):
                return  # over today's cap — _meter_voice has ended the session
            await self._gateway.send_audio(payload, ts_ms=ts)
        elif tag == FrameTag.INPUT_VIDEO:
            metrics.FRAMES_IN.labels(kind="video").inc()
            self._frames_in_video += 1
            now = time.monotonic()
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
        self._last_activity = time.monotonic()  # any control msg = still alive
        if mtype == "text":
            text = (message.get("text") or "").strip()
            if text:
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
            await self._send_state("idle")
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
        elif mtype == "device_update":
            # The client switched its active capture device mid-session (e.g.
            # glasses connected by voice AFTER hello and became the camera).
            # Re-pick the frame-wait budget from the NEW camera kind, so a
            # photo-trigger glasses capture gets the long budget it needs —
            # without this the session kept the hello-time "phone" budget and
            # cut off every glasses photo (device-proven 2026-07-11).
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
        max_s = self._settings.max_session_seconds
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

    async def _attach_frame_if_fresh(self) -> None:
        """Forward the latest cached camera frame once, if it is recent.

        Used at the start of a typed turn so visual questions still work when
        continuous frame streaming is gated off (``vision_frame_mode`` !=
        ``continuous``). Stale frames (camera lowered/off) are skipped.
        """
        if self._gateway is None or self._orchestrator is None:
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
        await self._gateway.interrupt()
        await self._send_state("listening")

    # -- Event pump (gateway -> client) --------------------------------------

    async def _event_pump(self) -> None:
        """Translate gateway events into server messages until stream end."""
        async for event in self._gateway.events():
            await self._handle_event(event)

    async def _handle_event(self, event: GatewayEvent) -> None:
        """Map one :class:`GatewayEvent` to a ``PROTOCOL.md`` server message."""
        if event.type == EventType.TRANSCRIPT:
            assert isinstance(event, TranscriptEvent)
            await self._send_json(
                {
                    "type": "transcript",
                    "role": event.role,
                    "text": event.text,
                    "final": event.final,
                }
            )
            if event.final and event.text:
                await repo_safe_transcript(
                    self.session_id, event.role, event.text
                )
        elif event.type == EventType.AUDIO_START:
            assert isinstance(event, AudioStartEvent)
            await self._send_state("speaking")
            await self._send_json({"type": "audio_start"})
        elif event.type == EventType.AUDIO_CHUNK:
            assert isinstance(event, AudioChunkEvent)
            await self._send_audio_frame(event.pcm)
        elif event.type == EventType.AUDIO_END:
            assert isinstance(event, AudioEndEvent)
            await self._send_json({"type": "audio_end"})
        elif event.type == EventType.TOOL_CALL:
            assert isinstance(event, ToolCallEvent)
            await self._spawn_tool_call(event)
        elif event.type == EventType.TURN_COMPLETE:
            assert isinstance(event, TurnCompleteEvent)
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
        """Emit a ``state`` server message."""
        await self._send_json({"type": "state", "value": value})

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
                plan = self._plan_name or get_settings().default_plan
                upsell = "" if plan == "pro" else " Upgrade for more."
                await self._send_error(
                    "quota_exceeded",
                    f"You've used today's {cap // 60} minutes of voice on the "
                    f"{plan} plan.{upsell}",
                    fatal=True,
                )
                return False
            if self._voice_capped:
                return False

        if self._voice_pending_s >= _VOICE_FLUSH_EVERY_S:
            await self._flush_voice_usage()
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
        if self._voice_pending_s < 1:
            return
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
            logger.warning("quota.voice_flush_failed", error=str(exc))
            return
        self._voice_used_s += whole
        self._voice_pending_s -= whole

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
                row = await repo.get_daily_usage(
                    db,
                    user_key=self._usage_key(),
                    day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                )
                self._voice_used_s = float(row.voice_seconds if row else 0)
                # Resolve the caps-bearing plan here, in the same one-shot DB
                # trip: the alternative is a query per audio frame. A signed-in
                # user gets their subscription's plan; anonymous falls back to
                # the default inside active_plan_name.
                from app.modules.billing import service as billing

                self._plan_name = await billing.active_plan_name(db, self._user_id)
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
        # Bill the speech since the last flush. Without this every session under
        # _VOICE_FLUSH_EVERY_S would be free, and a user could talk all day in
        # 14-second bursts — the cap would count nothing.
        await self._flush_voice_usage()
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


async def repo_safe_transcript(session_id: str, role: str, text: str) -> None:
    """Persist a transcript segment, swallowing storage errors.

    Transcripts are convenience history; a DB hiccup must not interrupt the
    live conversation, so failures are logged and dropped.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        try:
            await repo.add_transcript(
                db, role=role, text=text, session_id=session_id
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("transcript.persist_failed", error=str(exc))
