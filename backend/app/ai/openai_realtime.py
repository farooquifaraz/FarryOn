"""OpenAI Realtime adapter (``openai``).

Imports are guarded so this module loads without the ``openai`` package; the
dependency and API key are only required at :meth:`connect`. The adapter
normalizes the Realtime event stream into the gateway's
:class:`~app.ai.events.GatewayEvent` vocabulary.

Reference: ``AsyncOpenAI().beta.realtime.connect(model=...)`` yields a
connection with ``session.update`` / ``input_audio_buffer.append`` /
``conversation.item.create`` / ``response.create`` plus an async event iterator.
Input audio is base64 PCM16; the protocol's 16 kHz mono input and 24 kHz output
match the Realtime defaults (``pcm16``).
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AIGateway, ToolSpec
from app.ai.events import (
    AudioChunkEvent,
    AudioEndEvent,
    AudioStartEvent,
    ErrorEvent,
    GatewayEvent,
    ToolCallEvent,
    TranscriptEvent,
    TurnCompleteEvent,
)
from app.config import get_settings
from app.logging_conf import get_logger

logger = get_logger(__name__)


class OpenAIRealtimeGateway(AIGateway):
    """Adapter over OpenAI Realtime sessions."""

    provider = "openai"

    def __init__(
        self,
        *,
        system_prompt: str,
        tools: list[ToolSpec],
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            system_prompt=system_prompt,
            tools=tools,
            model=model or settings.openai_realtime_model,
        )
        self._api_key = settings.openai_api_key
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._conn: Any = None
        self._conn_cm: Any = None
        self._recv_task: asyncio.Task[None] | None = None
        self._audio_open = False
        self._closed = False

    def _tool_definitions(self) -> list[dict[str, Any]]:
        """Render tools in the Realtime ``session.tools`` format."""
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self.tools
        ]

    async def connect(self) -> None:
        """Open the Realtime connection and configure the session.

        Raises:
            RuntimeError: If ``openai`` is missing or no API key is set.
        """
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "openai is not installed; `pip install openai` "
                "or set AI_PROVIDER=mock."
            ) from exc
        except BaseException as exc:  # pragma: no cover - broken SDK/native dep
            raise RuntimeError(
                f"failed to import openai: {exc!r}; "
                "fix the install or set AI_PROVIDER=mock."
            ) from exc

        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")

        client = AsyncOpenAI(api_key=self._api_key)
        # OpenAI disabled the old beta Realtime "shape" (close 4000
        # invalid_request_error.beta_api_shape_disabled). Use the GA Realtime
        # API: prefer the GA namespace (client.realtime), fall back to beta on
        # older SDKs, and send a GA-shaped session object.
        realtime_ns = getattr(client, "realtime", None) or client.beta.realtime

        ga_session = {
            "type": "realtime",
            "instructions": self.system_prompt,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 16000},
                    "turn_detection": {"type": "server_vad"},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": "alloy",
                },
            },
            "tools": self._tool_definitions(),
            "tool_choice": "auto",
        }

        last_exc: Exception | None = None
        # Configured model first, then the GA default; de-duplicated.
        for model in dict.fromkeys([self.model, "gpt-realtime"]):
            try:
                cm = realtime_ns.connect(model=model)
                conn = await cm.__aenter__()
                await conn.session.update(session=ga_session)
            except Exception as exc:  # noqa: BLE001 - try the next model
                last_exc = exc
                logger.warning(
                    "openai.connect_attempt_failed",
                    model=model,
                    error=repr(exc),
                )
                continue
            self._conn_cm = cm
            self._conn = conn
            self.model = model
            self._recv_task = asyncio.create_task(self._receive_loop())
            logger.info("openai.connected", model=model)
            return

        raise RuntimeError(f"OpenAI Realtime connect failed: {last_exc!r}")

    async def _receive_loop(self) -> None:
        """Translate the Realtime event stream into gateway events."""
        try:
            async for event in self._conn:
                await self._handle_event(event)
        except asyncio.CancelledError:  # pragma: no cover - cancellation path
            raise
        except Exception as exc:  # pragma: no cover - network/runtime
            logger.error("openai.receive_error", error=str(exc))
            await self._queue.put(
                ErrorEvent(code="provider_error", message=str(exc), fatal=True)
            )
        finally:
            await self._queue.put(None)

    async def _handle_event(self, event: Any) -> None:
        """Decode a single Realtime server event by its ``type``."""
        etype = getattr(event, "type", "")

        if etype in ("response.output_audio.delta", "response.audio.delta"):
            if not self._audio_open:
                self._audio_open = True
                await self._queue.put(AudioStartEvent())
            pcm = base64.b64decode(getattr(event, "delta", "") or "")
            if pcm:
                await self._queue.put(AudioChunkEvent(pcm=pcm))

        elif etype in ("response.output_audio.done", "response.audio.done"):
            if self._audio_open:
                self._audio_open = False
                await self._queue.put(AudioEndEvent())

        elif etype in (
            "response.output_audio_transcript.delta",
            "response.audio_transcript.delta",
            "response.output_text.delta",
            "response.text.delta",
        ):
            delta = getattr(event, "delta", "") or ""
            if delta:
                await self._queue.put(
                    TranscriptEvent(role="assistant", text=delta)
                )

        elif etype == (
            "conversation.item.input_audio_transcription.completed"
        ):
            transcript = getattr(event, "transcript", "") or ""
            if transcript:
                await self._queue.put(
                    TranscriptEvent(role="user", text=transcript, final=True)
                )

        elif etype == "response.function_call_arguments.done":
            raw_args = getattr(event, "arguments", "") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
            await self._queue.put(
                ToolCallEvent(
                    id=str(getattr(event, "call_id", "")),
                    name=str(getattr(event, "name", "")),
                    args=args,
                )
            )

        elif etype == "response.done":
            await self._queue.put(TurnCompleteEvent())

        elif etype == "error":
            err = getattr(event, "error", None)
            message = getattr(err, "message", "unknown error") if err else "error"
            await self._queue.put(
                ErrorEvent(code="provider_error", message=str(message))
            )

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        """Append input audio to the Realtime input buffer."""
        if self._conn is None:
            return
        await self._conn.input_audio_buffer.append(
            audio=base64.b64encode(pcm).decode("ascii")
        )

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        """OpenAI Realtime does not accept live video frames; ignored.

        Vision can be added by attaching image content to a conversation item;
        for the realtime audio path this is a no-op.
        """
        return None

    async def send_text(self, text: str) -> None:
        """Create a user message item and request a response."""
        if self._conn is None:
            return
        await self._conn.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
        await self._conn.response.create()

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        """Return a function-call output item and request continuation."""
        if self._conn is None:
            return
        payload = {"result": result} if ok else {"error": result}
        await self._conn.conversation.item.create(
            item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(payload, default=str),
            }
        )
        await self._conn.response.create()

    async def events(self) -> AsyncIterator[GatewayEvent]:
        """Yield normalized gateway events until the session closes."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def interrupt(self) -> None:
        """Cancel the in-flight response (barge-in)."""
        if self._conn is None:
            return
        if self._audio_open:
            self._audio_open = False
            await self._queue.put(AudioEndEvent())
        try:
            await self._conn.response.cancel()
        except Exception:  # pragma: no cover - best-effort
            pass

    async def close(self) -> None:
        """Tear down the receive task and connection (idempotent)."""
        if self._closed:
            return
        self._closed = True
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._conn_cm is not None:
            try:
                await self._conn_cm.__aexit__(None, None, None)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        await self._queue.put(None)
