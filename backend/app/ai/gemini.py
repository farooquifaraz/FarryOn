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
from app.observability import metrics

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
        # Cumulative per-turn transcripts (deltas are appended; the client
        # replaces the live line with each cumulative emit, then finalizes).
        self._assistant_buf = ""
        self._user_buf = ""
        # Cumulative billed tokens this session (from usage_metadata), for the
        # cost-visibility log.
        self._tokens_total = 0

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
        config_kwargs: dict[str, Any] = dict(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=self.system_prompt)]
            ),
            tools=[types.Tool(function_declarations=function_declarations)],
            # Ask the provider for word-level transcripts of BOTH sides. On a
            # native-audio model the spoken words arrive here — NOT as
            # ``model_turn.parts[].text`` (which is the model's private
            # reasoning). Without this we have no clean transcript to show.
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        # Keep the model's chain-of-thought OUT of the response stream so it can
        # never leak into the on-screen transcript. Guarded: a model/SDK that
        # rejects ``thinking_config`` still connects (we just skip thoughts in
        # the receive loop instead).
        try:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                include_thoughts=False
            )
        except Exception:  # noqa: BLE001 - field optional across SDK versions
            pass
        # Hands-free: use the model's AUTOMATIC voice activity detection so the
        # user never has to press a button — they just talk. Echo (the model
        # hearing its own spoken reply) is prevented on the client, which stops
        # streaming mic audio while the assistant is speaking (half-duplex), so
        # automatic VAD never re-triggers on the TTS.
        try:
            config_kwargs["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                ),
            )
        except Exception:  # noqa: BLE001 - field optional across SDK versions
            pass
        # Cost control: cap what gets re-billed every turn. Live re-bills the
        # WHOLE session history on each turn, so a long chat (and its piled-up
        # audio/video tokens) snowballs. A sliding window keeps only recent
        # context past the trigger, cutting text-input tokens sharply. Bonus:
        # it also lifts the native-audio 15-minute session limit. Guarded like
        # the other optional fields so an older SDK still connects.
        s = get_settings()
        if s.context_compression_enabled:
            try:
                config_kwargs["context_window_compression"] = (
                    types.ContextWindowCompressionConfig(
                        trigger_tokens=s.context_trigger_tokens,
                        sliding_window=types.SlidingWindow(
                            target_tokens=s.context_target_tokens
                        ),
                    )
                )
            except Exception:  # noqa: BLE001 - field optional across SDK versions
                pass
        return types.LiveConnectConfig(**config_kwargs)

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

        from google.genai import types  # type: ignore[import-not-found]

        last_exc: Exception | None = None
        tried: set[tuple[str, str]] = set()

        async def _open(api_version: str, model: str) -> bool:
            """Try one (api_version, model) pair; return True on success."""
            nonlocal last_exc
            try:
                client = genai.Client(
                    api_key=self._api_key,
                    http_options=types.HttpOptions(api_version=api_version),
                )
                cm = client.aio.live.connect(
                    model=model, config=self._build_config()
                )
                session = await cm.__aenter__()
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                last_exc = exc
                logger.warning(
                    "gemini.connect_attempt_failed",
                    model=model,
                    api_version=api_version,
                    error=repr(exc),
                )
                return False
            self._session_cm = cm
            self._session = session
            self.model = model
            logger.info(
                "gemini.connected", model=model, api_version=api_version
            )
            return True

        # 1) The configured model, on both Live channels (v1beta is the
        #    Developer API default; v1alpha covers preview models).
        for version in ("v1beta", "v1alpha"):
            tried.add((version, self.model))
            if await _open(version, self.model):
                self._recv_task = asyncio.create_task(self._receive_loop())
                return

        # 2) Self-heal: the set of Live (bidiGenerateContent) models varies by
        #    key/project, so discover what THIS key actually exposes and try
        #    each, rather than hard-coding a name that may not exist.
        discovered: list[str] = []
        try:
            lister = genai.Client(api_key=self._api_key)
            for m in lister.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if "bidiGenerateContent" in actions:
                    discovered.append(getattr(m, "name", "").split("/")[-1])
        except Exception as exc:  # noqa: BLE001 - discovery is best-effort
            logger.warning("gemini.model_discovery_failed", error=repr(exc))

        for model in discovered:
            for version in ("v1beta", "v1alpha"):
                if (version, model) in tried:
                    continue
                tried.add((version, model))
                if await _open(version, model):
                    self._recv_task = asyncio.create_task(
                        self._receive_loop()
                    )
                    return

        raise RuntimeError(
            "no usable Gemini Live model for this key "
            f"(discovered={discovered or 'none'}); last error: {last_exc!r}"
        )

    async def _receive_loop(self) -> None:
        """Translate the provider stream into gateway events.

        ``session.receive()`` yields the messages for a SINGLE model turn and
        then completes — the SDK breaks the iterator after ``turn_complete``.
        To keep a multi-turn conversation alive we must call it again for every
        subsequent turn, so the inner ``async for`` is wrapped in an outer loop
        that runs until the gateway is closed or the upstream errors. Without
        this the session ends after the first reply and later turns get no
        response.
        """
        try:
            while not self._closed:
                saw_message = False
                async for message in self._session.receive():
                    saw_message = True
                    await self._handle_message(message)
                if not saw_message:
                    # An exhausted/empty turn means the upstream session ended;
                    # stop instead of spinning on a dead receive(). Log it (this
                    # was silent, so a mid-conversation end looked like a hang):
                    # the queue-None below ends events(), the session closes, and
                    # the app reconnects.
                    logger.info(
                        "gemini.session_ended",
                        reason="empty_turn",
                        tokens_total=self._tokens_total,
                    )
                    break
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
        # Cost visibility: the provider reports per-turn token counts in
        # usage_metadata. Log + meter them so a session's real cost is visible
        # and the frame/context savings can be confirmed in numbers.
        usage = getattr(message, "usage_metadata", None)
        if usage is not None:
            self._record_usage(usage)

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
                    # ``part.text`` here is the model's private reasoning, not
                    # the words it speaks. We deliberately do NOT surface it —
                    # the spoken transcript comes from ``output_transcription``.

            # User speech → assistant speech transcripts (cumulative deltas).
            in_tx = getattr(server_content, "input_transcription", None)
            in_text = getattr(in_tx, "text", None) if in_tx else None
            if in_text:
                self._user_buf += in_text
                await self._queue.put(
                    TranscriptEvent(
                        role="user", text=self._user_buf, final=False
                    )
                )

            out_tx = getattr(server_content, "output_transcription", None)
            out_text = getattr(out_tx, "text", None) if out_tx else None
            if out_text:
                self._assistant_buf += out_text
                await self._queue.put(
                    TranscriptEvent(
                        role="assistant",
                        text=self._assistant_buf,
                        final=False,
                    )
                )

            if getattr(server_content, "interrupted", False):
                # Server-side barge-in: stop stale audio and finalize the
                # partial lines, but let the model start its next turn fresh.
                await self._finalize_turn(turn_complete=False)
            elif getattr(server_content, "turn_complete", False):
                await self._finalize_turn(turn_complete=True)

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

    def _record_usage(self, usage: Any) -> None:
        """Meter + log token usage from a Gemini ``usage_metadata`` block."""
        total = int(getattr(usage, "total_token_count", 0) or 0)
        prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
        resp = int(getattr(usage, "response_token_count", 0) or 0)
        if not (total or prompt or resp):
            return
        self._tokens_total += total
        if total:
            metrics.TOKENS_USED.labels(kind="total").inc(total)
        if prompt:
            metrics.TOKENS_USED.labels(kind="input").inc(prompt)
        if resp:
            metrics.TOKENS_USED.labels(kind="output").inc(resp)
        logger.info(
            "gemini.usage",
            turn_total=total,
            input=prompt,
            output=resp,
            session_total=self._tokens_total,
        )

    async def _finalize_turn(self, *, turn_complete: bool) -> None:
        """Close out the current turn: end audio, finalize transcripts.

        Args:
            turn_complete: ``True`` for a normal end-of-turn (emits
                ``TurnComplete`` so the client returns to *listening*);
                ``False`` for a server-side interruption, where the model
                immediately begins a fresh turn.
        """
        if self._audio_open:
            self._audio_open = False
            await self._queue.put(AudioEndEvent())
        if self._user_buf:
            await self._queue.put(
                TranscriptEvent(role="user", text=self._user_buf, final=True)
            )
            self._user_buf = ""
        if self._assistant_buf:
            await self._queue.put(
                TranscriptEvent(
                    role="assistant", text=self._assistant_buf, final=True
                )
            )
            self._assistant_buf = ""
        if turn_complete:
            await self._queue.put(TurnCompleteEvent())

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

    async def send_activity_start(self) -> None:
        """Open a manual activity window — the user started speaking."""
        if self._session is None:
            return
        from google.genai import types  # type: ignore[import-not-found]

        await self._session.send_realtime_input(
            activity_start=types.ActivityStart()
        )

    async def send_activity_end(self) -> None:
        """Close the manual activity window — the user stopped; reply now."""
        if self._session is None:
            return
        from google.genai import types  # type: ignore[import-not-found]

        await self._session.send_realtime_input(
            activity_end=types.ActivityEnd()
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
        output to stop forwarding stale audio immediately and reset the partial
        transcript so the next turn starts clean.
        """
        self._user_buf = ""
        if self._audio_open:
            self._audio_open = False
            await self._queue.put(AudioEndEvent())
        if self._assistant_buf:
            await self._queue.put(
                TranscriptEvent(
                    role="assistant", text=self._assistant_buf, final=True
                )
            )
            self._assistant_buf = ""

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
