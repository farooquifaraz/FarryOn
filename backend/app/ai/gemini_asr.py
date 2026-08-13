"""Streaming speech recognition — the words as they are spoken.

This is step one of the translate cascade, and getting it wrong is what made
"live translation" not live.

**What came before, and why it failed.** Step one was the *translate* model,
asked for an English target on the theory that it would then write the source
language down faithfully. It did not: an Arabic paragraph came back as fluent
Vietnamese, with Egypt turned into America and the mosque into a church (S23,
2026-08-13); the day before, the same audio came back as English. Asked to
translate, it translated — into whatever it felt like — and every later step
inherited the answer.

Replacing it with **batch** transcription fixed the words but not the feel: a
whole utterance had to end before a single round trip could start, so nothing
was spoken until the speaker stopped. On a 31-second paragraph the first
translated word arrived eleven seconds *after* the last spoken one.

Trying to cut that up by slicing the AUDIO at sentence boundaries found in the
TEXT made it worse, and the reason is worth keeping: **the transcript lags the
audio by an unknown amount**, so a cut made where the text ends a sentence does
not land where the sound does. Chunks overlapped and words were split — the
screen showed the same clause twice and "عمي كريم" (my uncle Kareem) lost its
first half and came out as a woman named Reem.

**What this does instead.** A live session transcribes continuously — measured
at 77 deltas starting 3.4 s into a 31-second clip, in correct Arabic, with no
translation config anywhere near it. Because the words stream, nothing has to
be cut out of the audio at all: segmentation happens on TEXT ONLY, where
cutting is exact and reversible. That is the whole trick, and it is what the
simultaneous-translation literature does too (segment the hypothesis, not the
waveform).

The model is asked to stay silent. It is a listener; its own voice is nobody's
business here, and speech tokens cost six times what listening does.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AIGateway, ToolSpec
from app.ai.events import (
    ErrorEvent,
    GatewayEvent,
    TranscriptEvent,
)
from app.config import get_settings
from app.logging_conf import get_logger

logger = get_logger(__name__)

#: Characters that end a sentence, across the scripts this product is used in.
_SENTENCE_ENDINGS = ".?!।॥؟。！？…"

#: Shortest run of text that may be closed off by its own full stop, so "Yes."
#: does not become an utterance with its own translation and its own voice.
_MIN_SENTENCE_CHARS = 25

#: Break a speaker who never punctuates, so the screen keeps moving.
_MAX_UTTERANCE_CHARS = 260

#: How long the transcript must be quiet before an unfinished thought is closed
#: anyway. Long enough to ride out the gap between two clauses.
_QUIET_GAP_S = 1.8

_SILENT_INSTRUCTION = (
    "You are a silent transcriber. Never speak, never answer, never comment, "
    "never translate. Produce no output of any kind. You exist only so that "
    "the incoming audio is transcribed."
)


class GeminiStreamingASR(AIGateway):
    """A live session used purely as a speech recogniser."""

    provider = "gemini_asr"

    def __init__(
        self,
        *,
        model: str | None = None,
        system_prompt: str = "",
        tools: list[ToolSpec] | None = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            system_prompt="",
            tools=[],
            model=model or settings.translate_asr_model,
        )
        self._api_key = settings.gemini_api_key
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._session: Any = None
        self._session_cm: Any = None
        self._recv_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._closed = False
        self._buf = ""
        self._lang: str | None = None
        self._last_delta_at = 0.0
        #: Counted, not forwarded. If it ever rises the silence instruction has
        #: stopped working and we are paying for speech nobody hears.
        self._spoke_anyway = 0

    # -- Setup ---------------------------------------------------------------

    def _build_config(self) -> Any:
        from google.genai import types

        return types.LiveConnectConfig(
            # TEXT is rejected by every live model that transcribes well, so the
            # audio reply is accepted and thrown away. The instruction below is
            # what actually keeps it quiet.
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=_SILENT_INSTRUCTION,
        )

    async def connect(self) -> None:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError("google-genai is not installed.") from exc
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")

        last: Exception | None = None
        for api_version in ("v1alpha", "v1beta"):
            try:
                client = genai.Client(
                    api_key=self._api_key,
                    http_options=types.HttpOptions(api_version=api_version),
                )
                cm = client.aio.live.connect(
                    model=self.model, config=self._build_config()
                )
                self._session = await cm.__aenter__()
                self._session_cm = cm
                logger.info(
                    "gemini_asr.connected", model=self.model, api_version=api_version
                )
                self._last_delta_at = time.monotonic()
                self._recv_task = asyncio.create_task(self._receive_loop())
                self._watchdog_task = asyncio.create_task(self._quiet_watchdog())
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                logger.warning(
                    "gemini_asr.connect_attempt_failed",
                    model=self.model,
                    api_version=api_version,
                    error=repr(exc),
                )
        raise RuntimeError(f"could not open {self.model!r}; last error: {last!r}")

    # -- Sending -------------------------------------------------------------

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        session = self._session
        if session is None or self._closed:
            return
        from google.genai import types

        with contextlib.suppress(Exception):
            await session.send_realtime_input(
                audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
            )

    async def send_text(self, text: str) -> None:
        return None

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        return None

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        return None

    # -- Receiving -----------------------------------------------------------

    async def _receive_loop(self) -> None:
        try:
            while not self._closed:
                saw = False
                try:
                    async for message in self._session.receive():
                        saw = True
                        await self._handle(message)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    if self._closed:
                        break
                    logger.warning("gemini_asr.stream_error", error=str(exc))
                    await self._queue.put(
                        ErrorEvent(
                            code="provider_error",
                            message="Speech recognition stopped unexpectedly.",
                            fatal=True,
                        )
                    )
                    break
                if not saw:
                    break
        except asyncio.CancelledError:
            raise
        finally:
            await self._queue.put(None)

    async def _handle(self, message: Any) -> None:
        content = getattr(message, "server_content", None)
        if content is None:
            return

        # It was told not to speak. Count it if it does — that is money.
        turn = getattr(content, "model_turn", None)
        if turn is not None:
            for part in getattr(turn, "parts", []) or []:
                inline = getattr(part, "inline_data", None)
                if getattr(inline, "data", None):
                    self._spoke_anyway += 1

        tx = getattr(content, "input_transcription", None)
        text = getattr(tx, "text", None) if tx else None
        if not text:
            return
        self._last_delta_at = time.monotonic()
        self._buf += text
        code = getattr(tx, "language_code", None)
        if code:
            self._lang = code
        await self._queue.put(
            TranscriptEvent(role="user", text=self._buf, final=False, lang=self._lang)
        )

        # Segmentation happens HERE and only here — on the text. The audio is
        # never cut, which is what stopped words being split in half.
        cut = self._last_sentence_end()
        if cut is not None and cut >= _MIN_SENTENCE_CHARS:
            await self._close(cut)
        elif len(self._buf) > _MAX_UTTERANCE_CHARS:
            await self._close(len(self._buf))

    def _last_sentence_end(self) -> int | None:
        for i in range(len(self._buf) - 1, -1, -1):
            if self._buf[i] in _SENTENCE_ENDINGS:
                return i + 1
        return None

    async def _close(self, cut: int) -> None:
        """Emit everything up to `cut` as final; keep the rest for next time."""
        head, self._buf = self._buf[:cut].strip(), self._buf[cut:].lstrip()
        if not head:
            return
        await self._queue.put(
            TranscriptEvent(role="user", text=head, final=True, lang=self._lang)
        )

    async def _quiet_watchdog(self) -> None:
        """Close an unfinished thought once the speaker has clearly stopped.

        Someone who trails off without a full stop would otherwise never be
        translated at all.
        """
        try:
            while not self._closed:
                await asyncio.sleep(0.25)
                if not self._buf:
                    continue
                if time.monotonic() - self._last_delta_at >= _QUIET_GAP_S:
                    await self._close(len(self._buf))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("gemini_asr.watchdog_error", error=repr(exc))
            if not self._closed:
                self._watchdog_task = asyncio.create_task(self._quiet_watchdog())

    # -- Lifecycle -----------------------------------------------------------

    async def events(self) -> AsyncIterator[GatewayEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def interrupt(self) -> None:
        return None

    async def close(self) -> None:
        if self._closed:
            return
        with contextlib.suppress(Exception):
            if self._buf:
                await self._close(len(self._buf))
        self._closed = True
        if self._spoke_anyway:
            logger.warning(
                "gemini_asr.model_spoke", chunks=self._spoke_anyway,
            )
        for task in (self._watchdog_task, self._recv_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._watchdog_task = self._recv_task = None
        if self._session_cm is not None:
            with contextlib.suppress(Exception):
                await self._session_cm.__aexit__(None, None, None)
        self._session = self._session_cm = None
        await self._queue.put(None)
