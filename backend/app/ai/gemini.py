"""Gemini Live adapter (``google-genai``).

Imports are guarded so this module loads even when ``google-genai`` is not
installed; the dependency and API key are only required when :meth:`connect` is
called. The adapter normalizes the Gemini Live session stream into the gateway's
:class:`~app.ai.events.GatewayEvent` vocabulary.

Reference: ``google.genai`` ``client.aio.live.connect(...)`` yields a session
exposing ``send`` / ``send_realtime_input`` / ``receive``. The exact surface
evolves across SDK versions; the receive loop below defensively reads the common
fields (``server_content`` for audio/text, ``tool_call`` for function calls).
"""

from __future__ import annotations

import asyncio
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

# 16 kHz mono PCM input per PROTOCOL.md; Gemini accepts 16 kHz input audio.
_INPUT_MIME = "audio/pcm;rate=16000"
_VIDEO_MIME = "image/jpeg"


class GeminiGateway(AIGateway):
    """Adapter over Gemini Live realtime sessions."""

    provider = "gemini"

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
            model=model or settings.gemini_model,
        )
        self._api_key = settings.gemini_api_key
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._session: Any = None
        self._session_cm: Any = None
        self._recv_task: asyncio.Task[None] | None = None
        self._audio_open = False
        self._closed = False

    def _build_config(self) -> Any:
        """Construct the ``LiveConnectConfig`` with tools + system prompt."""
        from google.genai import types  # type: ignore[import-not-found]

        function_declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
            )
            for t in self.tools
        ]
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=self.system_prompt)]
            ),
            tools=[types.Tool(function_declarations=function_declarations)],
        )

    async def connect(self) -> None:
        """Open the Gemini Live session and start the receive loop.

        Raises:
            RuntimeError: If ``google-genai`` is missing or no API key is set.
        """
        try:
            from google import genai  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "google-genai is not installed; `pip install google-genai` "
                "or set AI_PROVIDER=mock."
            ) from exc
        except BaseException as exc:  # pragma: no cover - broken SDK/native dep
            # Some transitive native deps (e.g. cryptography) can fail to load
            # with non-ImportError errors; surface a clean message either way.
            raise RuntimeError(
                f"failed to import google-genai: {exc!r}; "
                "fix the install or set AI_PROVIDER=mock."
            ) from exc

        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")

        client = genai.Client(api_key=self._api_key)
        self._session_cm = client.aio.live.connect(
            model=self.model, config=self._build_config()
        )
        self._session = await self._session_cm.__aenter__()
        self._recv_task = asyncio.create_task(self._receive_loop())
        logger.info("gemini.connected", model=self.model)

    async def _receive_loop(self) -> None:
        """Translate the provider stream into gateway events."""
        try:
            async for message in self._session.receive():
                await self._handle_message(message)
        except asyncio.CancelledError:  # pragma: no cover - cancellation path
            raise
        except Exception as exc:  # pragma: no cover - network/runtime
            logger.error("gemini.receive_error", error=str(exc))
            await self._queue.put(
                ErrorEvent(code="provider_error", message=str(exc), fatal=True)
            )
        finally:
            await self._queue.put(None)

    async def _handle_message(self, message: Any) -> None:
        """Decode a single Gemini Live server message."""
        server_content = getattr(message, "server_content", None)
        if server_content is not None:
            model_turn = getattr(server_content, "model_turn", None)
            if model_turn is not None:
                for part in getattr(model_turn, "parts", []) or []:
                    inline = getattr(part, "inline_data", None)
                    if inline is not None and getattr(inline, "data", None):
                        if not self._audio_open:
                            self._audio_open = True
                            await self._queue.put(AudioStartEvent())
                        await self._queue.put(AudioChunkEvent(pcm=inline.data))
                    text = getattr(part, "text", None)
                    if text:
                        await self._queue.put(
                            TranscriptEvent(role="assistant", text=text)
                        )
            if getattr(server_content, "turn_complete", False):
                if self._audio_open:
                    self._audio_open = False
                    await self._queue.put(AudioEndEvent())
                await self._queue.put(TurnCompleteEvent())

        tool_call = getattr(message, "tool_call", None)
        if tool_call is not None:
            for fc in getattr(tool_call, "function_calls", []) or []:
                await self._queue.put(
                    ToolCallEvent(
                        id=str(getattr(fc, "id", "") or getattr(fc, "name", "")),
                        name=fc.name,
                        args=dict(getattr(fc, "args", {}) or {}),
                    )
                )

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        """Stream input audio to the live session."""
        if self._session is None:
            return
        from google.genai import types  # type: ignore[import-not-found]

        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=_INPUT_MIME)
        )

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        """Stream a JPEG video frame to the live session."""
        if self._session is None:
            return
        from google.genai import types  # type: ignore[import-not-found]

        await self._session.send_realtime_input(
            video=types.Blob(data=jpeg, mime_type=_VIDEO_MIME)
        )

    async def send_text(self, text: str) -> None:
        """Send a typed user turn."""
        if self._session is None:
            return
        await self._session.send_client_content(
            turns={"role": "user", "parts": [{"text": text}]},
            turn_complete=True,
        )

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        """Return a function-call result to the model."""
        if self._session is None:
            return
        from google.genai import types  # type: ignore[import-not-found]

        response = types.FunctionResponse(
            id=call_id or None,
            name=name,
            response={"result": result} if ok else {"error": result},
        )
        await self._session.send_tool_response(function_responses=[response])

    async def events(self) -> AsyncIterator[GatewayEvent]:
        """Yield normalized gateway events until the session closes."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def interrupt(self) -> None:
        """Best-effort barge-in.

        Gemini Live performs server-side VAD interruption when new input audio
        arrives; there is no explicit cancel call, so we drop locally buffered
        output to stop forwarding stale audio immediately.
        """
        if self._audio_open:
            self._audio_open = False
            await self._queue.put(AudioEndEvent())

    async def close(self) -> None:
        """Tear down the receive task and live session (idempotent)."""
        if self._closed:
            return
        self._closed = True
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        await self._queue.put(None)
