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
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
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
from app.config import Settings
from app.db import repo
from app.prompts.system import build_system_prompt
from app.db.base import get_sessionmaker
from app.logging_conf import get_logger
from app.observability import metrics
from app.ws.frames import FrameTag, decode_frame, encode_frame

logger = get_logger(__name__)

PROTOCOL_VERSION = 1
_ANON_USER = "anonymous"


class Session:
    """Owns the lifecycle and concurrency for a single live connection."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        gateway_factory: Callable[[str | None, str | None], AIGateway],
        engine: ToolEngine,
        settings: Settings,
    ) -> None:
        self._ws = websocket
        # The gateway is built AFTER the handshake, once we know which provider
        # the client asked for (hello.provider) — see :meth:`_resolve_provider`.
        self._gateway_factory = gateway_factory
        self._gateway: AIGateway | None = None
        self._engine = engine
        self._settings = settings

        self.session_id: str = uuid.uuid4().hex
        self.resume_of: str | None = None
        self._user_id: int | None = None

        self._send_lock = asyncio.Lock()
        self._closing = False
        self._hello: dict[str, Any] | None = None
        self._orchestrator: Orchestrator | None = None
        # Tracks active tool-call tasks so they are awaited/cancelled cleanly.
        self._tool_tasks: set[asyncio.Task[Any]] = set()

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
                await self._send_error(
                    "provider_unavailable", repr(exc), fatal=True
                )
                reason = "connect_failed"
                return
            await self._persist_session_start()
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
            self._orchestrator = Orchestrator(
                engine=self._engine,
                gateway=self._gateway,
                sessionmaker=get_sessionmaker(),
                notify_client=self._send_json,
                session_id=self.session_id,
                user_id=self._user_id,
                web_search=web_search if isinstance(web_search, dict) else None,
            )

            read_task = asyncio.create_task(self._read_pump(), name="read_pump")
            event_task = asyncio.create_task(
                self._event_pump(), name="event_pump"
            )
            done, pending = await asyncio.wait(
                {read_task, event_task}, return_when=asyncio.FIRST_COMPLETED
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
            await self._gateway.send_audio(payload, ts_ms=ts)
        elif tag == FrameTag.INPUT_VIDEO:
            metrics.FRAMES_IN.labels(kind="video").inc()
            await self._gateway.send_video(payload, ts_ms=ts)
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
        if mtype == "text":
            text = (message.get("text") or "").strip()
            if text:
                await self._send_state("thinking")
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

    async def _persist_session_start(self) -> None:
        """Resolve the anonymous user and record the session start row."""
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            try:
                user = await repo.get_or_create_user(db, _ANON_USER)
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
            except Exception as exc:  # noqa: BLE001 - persistence is best-effort
                await db.rollback()
                logger.error("session.persist_failed", error=str(exc))

    async def _cleanup(self, reason: str) -> None:
        """Cancel tool tasks, close the gateway, mark the session ended."""
        if self._closing:
            return
        self._closing = True
        metrics.WS_DISCONNECTS.labels(reason=reason).inc()
        logger.info(
            "session.closing", session_id=self.session_id, reason=reason
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
