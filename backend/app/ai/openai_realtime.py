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

import array
import asyncio
import base64
import json
import math
import time
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

#: The phone streams 16 kHz PCM16 (PROTOCOL.md), but the GA Realtime API
#: requires input audio >= 24 kHz, so we upsample on the way in.
_CLIENT_INPUT_RATE = 16000
_REALTIME_INPUT_RATE = 24000

#: Don't attach a camera frame older than this (camera likely lowered/off).
_FRAME_MAX_AGE_SECONDS = 4.0


class _LinearResampler:
    """Streaming linear-interpolation resampler for PCM16 mono audio.

    Keeps a continuous fractional read position and the last sample of the
    previous chunk so consecutive ``process`` calls join seamlessly (no clicks
    at chunk boundaries). Cheap enough for a realtime mic stream and adds no
    native dependency (``audioop`` was removed in Python 3.13).
    """

    def __init__(self, in_rate: int, out_rate: int) -> None:
        self._step = in_rate / out_rate  # input samples advanced per output
        self._pos = 0.0  # next output's source index (-1 == previous tail)
        self._prev = 0  # last sample carried from the previous chunk

    def process(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        src = array.array("h")
        src.frombytes(pcm)
        n = len(src)
        if n == 0:
            return b""
        out = array.array("h")
        pos = self._pos
        # Emit outputs whose source position falls within [prev .. src[n-1]].
        while pos < n - 1:
            i = math.floor(pos)
            frac = pos - i
            a = self._prev if i < 0 else src[i]
            b = src[i + 1]
            out.append(int(a + (b - a) * frac))
            pos += self._step
        self._prev = src[n - 1]
        self._pos = pos - n  # rebase origin onto the next chunk
        return out.tobytes()


class OpenAIRealtimeGateway(AIGateway):
    """Adapter over OpenAI Realtime sessions."""

    provider = "openai"

    def __init__(
        self,
        *,
        system_prompt: str,
        tools: list[ToolSpec],
        model: str | None = None,
        api_key: str | None = None,
        websocket_base_url: str | None = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            system_prompt=system_prompt,
            tools=tools,
            model=model or settings.openai_realtime_model,
        )
        # ``api_key``/``websocket_base_url`` let OpenAI-compatible realtime
        # backends (e.g. Grok/xAI) reuse this adapter by only changing the
        # endpoint. Only the OpenAI provider falls back to the OpenAI key — a
        # subclass (Grok) must use its OWN key, never the OpenAI one, otherwise
        # we'd send an ``sk-...`` key to x.ai and get "Incorrect API key".
        if api_key is not None:
            self._api_key = api_key
        elif self.provider == "openai":
            self._api_key = settings.openai_api_key
        else:
            self._api_key = None
        self._ws_base_url = websocket_base_url
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._conn: Any = None
        self._conn_cm: Any = None
        self._recv_task: asyncio.Task[None] | None = None
        self._audio_open = False
        self._closed = False
        # Upsample the phone's 16 kHz mic stream to the 24 kHz the GA Realtime
        # API requires (shared by the Grok subclass, same OpenAI-compatible API).
        self._in_resampler = _LinearResampler(
            _CLIENT_INPUT_RATE, _REALTIME_INPUT_RATE
        )
        # Cumulative assistant transcript for the current turn. The Realtime API
        # streams *deltas*; we accumulate so the client always receives the full
        # line (the UI replaces the live line with each cumulative emit).
        self._assistant_buf = ""
        # Cumulative transcript of the USER's current spoken turn (from the
        # input-audio transcription stream), so their chat line fills in live.
        self._user_buf = ""
        # Latest camera frame (base64 JPEG) + when it arrived, so we can show
        # the model what the user is looking at on each turn — vision parity
        # with Gemini, which streams frames continuously.
        self._latest_frame_b64: str | None = None
        self._latest_frame_at: float = 0.0
        # Whether a model response is currently being generated. The GA API
        # rejects a second response.create while one is active ("Conversation
        # already has an active response in progress"), which happened when our
        # manual create raced the server. Tracked from response.created/.done
        # and set optimistically before we create, so we never double-create.
        self._response_active = False
        # Live vision via conversation image items + manual response creation.
        # Only OpenAI's GA Realtime supports this; xAI/Grok does not honour
        # create_response=false / input_image, so it stays on auto-response and
        # its voice path is left exactly as-is (no regression).
        self._vision_items = self.provider == "openai"

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
            raise RuntimeError(
                f"{self.provider.upper()} API key is not set "
                f"(set {self.provider.upper()}_API_KEY)."
            )

        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._ws_base_url:
            # Point the realtime websocket at an OpenAI-compatible endpoint
            # (e.g. wss://api.x.ai/v1 for Grok).
            client_kwargs["websocket_base_url"] = self._ws_base_url
        client = AsyncOpenAI(**client_kwargs)
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
                    "format": {
                        "type": "audio/pcm",
                        "rate": _REALTIME_INPUT_RATE,
                    },
                    # OpenAI: detect end-of-speech but DON'T auto-create the
                    # reply — we create it after attaching the current camera
                    # frame (vision parity with Gemini). Other providers keep
                    # the default server auto-response.
                    "turn_detection": (
                        {
                            "type": "server_vad",
                            # Less twitchy than the 0.5 default so a loud TV,
                            # room noise, or the assistant's own echo tail
                            # doesn't get committed as a phantom user turn (seen
                            # in real logs: AI answering itself / Whisper
                            # hallucinating "subscribe…" on non-speech). Needs a
                            # slightly clearer pause to end a turn, too.
                            "threshold": 0.6,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 600,
                            "create_response": False,
                            "interrupt_response": True,
                        }
                        if self._vision_items
                        else {
                            # Grok path: end the turn sooner so the (already
                            # slower) xAI model starts replying with less lag.
                            # Noisy rooms are covered by tap-to-talk now.
                            "type": "server_vad",
                            "threshold": 0.55,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 400,
                        }
                    ),
                    # Transcribe the user's speech so their side of the
                    # conversation shows in the chat too (parity with Gemini).
                    # Without this the
                    # conversation.item.input_audio_transcription.completed
                    # event never fires and only the assistant's text appears.
                    "transcription": {"model": "whisper-1"},
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
        # Configured model first, then the GA default (OpenAI only) — Grok and
        # other compatible endpoints have their own model names.
        candidates = [self.model]
        if self.provider == "openai":
            candidates.append("gpt-realtime")
        for model in dict.fromkeys(candidates):
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
                self._assistant_buf += delta
                await self._queue.put(
                    TranscriptEvent(
                        role="assistant",
                        text=self._assistant_buf,
                        final=False,
                    )
                )

        elif etype == "input_audio_buffer.committed" and self._vision_items:
            # The user finished an audio turn (server VAD). Auto-response is
            # off (OpenAI only), so we attach the current frame and create the
            # reply here — giving the model live vision on every spoken turn.
            await self._create_response_with_frame()

        elif etype == (
            "conversation.item.input_audio_transcription.delta"
        ):
            # Stream the user's own words as they're transcribed so their side
            # of the chat fills in live (cumulative, like the assistant line).
            delta = getattr(event, "delta", "") or ""
            if delta:
                self._user_buf += delta
                await self._queue.put(
                    TranscriptEvent(
                        role="user", text=self._user_buf, final=False
                    )
                )

        elif etype == (
            "conversation.item.input_audio_transcription.completed"
        ):
            transcript = (
                getattr(event, "transcript", "") or self._user_buf or ""
            )
            self._user_buf = ""
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

        elif etype == "response.created":
            self._response_active = True

        elif etype == "response.done":
            self._response_active = False
            if self._assistant_buf:
                await self._queue.put(
                    TranscriptEvent(
                        role="assistant",
                        text=self._assistant_buf,
                        final=True,
                    )
                )
                self._assistant_buf = ""
            await self._queue.put(TurnCompleteEvent())

        elif etype == "error":
            # An errored response won't emit response.done, so clear the active
            # flag here or the session would go silent (no new response allowed).
            self._response_active = False
            err = getattr(event, "error", None)
            message = getattr(err, "message", "unknown error") if err else "error"
            await self._queue.put(
                ErrorEvent(code="provider_error", message=str(message))
            )

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        """Append input audio to the Realtime input buffer."""
        if self._conn is None:
            return
        pcm24 = self._in_resampler.process(pcm)
        if not pcm24:
            return
        await self._conn.input_audio_buffer.append(
            audio=base64.b64encode(pcm24).decode("ascii")
        )

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        """Cache the latest camera frame.

        The Realtime audio stream can't accept continuous video, so instead of
        forwarding every frame we keep only the most recent one and attach it to
        the conversation when the user takes a turn (see
        :meth:`_create_response_with_frame`). This gives the model live vision —
        "what am I looking at?", "read this label" — matching Gemini.
        """
        if not jpeg:
            return
        self._latest_frame_b64 = base64.b64encode(jpeg).decode("ascii")
        self._latest_frame_at = time.monotonic()

    async def _attach_latest_frame(self) -> None:
        """Add the most recent camera frame to the conversation, if fresh.

        Only attaches a frame seen in the last few seconds so a stale image from
        before the camera was lowered/turned off is never sent.
        """
        if self._conn is None or not self._latest_frame_b64:
            return
        if (time.monotonic() - self._latest_frame_at) > _FRAME_MAX_AGE_SECONDS:
            return
        try:
            await self._conn.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": (
                                "data:image/jpeg;base64,"
                                f"{self._latest_frame_b64}"
                            ),
                        }
                    ],
                }
            )
        except Exception as exc:  # noqa: BLE001 - vision is best-effort
            logger.warning("openai.attach_frame_failed", error=repr(exc))

    async def _safe_response_create(self) -> None:
        """Ask the model to respond, unless a response is already in flight.

        The GA API rejects a second ``response.create`` while one is active. We
        set the flag optimistically (before the call) so two near-simultaneous
        triggers — e.g. a server-VAD commit racing our manual create — can't
        both fire and produce the "active response in progress" error.
        """
        if self._conn is None or self._response_active:
            return
        self._response_active = True
        try:
            await self._conn.response.create()
        except Exception:
            self._response_active = False
            raise

    async def _create_response_with_frame(self) -> None:
        """Attach the current frame (if any) then ask the model to respond."""
        if self._conn is None or self._response_active:
            return
        await self._attach_latest_frame()
        await self._safe_response_create()

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
        # OpenAI: attach the current camera frame so typed turns get vision too.
        if self._vision_items:
            await self._create_response_with_frame()
        else:
            await self._safe_response_create()

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
        await self._safe_response_create()

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
        self._assistant_buf = ""
        if self._audio_open:
            self._audio_open = False
            await self._queue.put(AudioEndEvent())
        try:
            await self._conn.response.cancel()
        except Exception:  # pragma: no cover - best-effort
            pass
        # The cancelled response is no longer active, so the next turn can
        # create one (response.done may not arrive for a cancelled response).
        self._response_active = False

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
