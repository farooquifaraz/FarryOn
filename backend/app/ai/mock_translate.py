"""Deterministic, network-free translate gateway.

The translate twin of :mod:`app.ai.mock`. It lets the whole translate path —
``hello.mode``, the session's translate branch, the wire messages, and the
Flutter translate screen — run end to end with **no API key and no cost**,
which is how ``ai/mock.py`` is already used by the test suite and by offline
development.

What it emits, per chunk of input audio, is one "utterance":

1. a partial then final ``TranscriptEvent(role="user")`` — what was *heard*,
   tagged with the language it was heard in;
2. a final ``TranscriptEvent(role="assistant")`` — the *translation*, tagged
   with the target language;
3. a short burst of synthetic 24 kHz PCM, so the client really has audio to
   play.

Every third utterance is deliberately **already in the target language**. The
real model stays silent there (``echoTargetLanguage: false``), and that silence
is the single most confusing thing about this feature — a user reads it as
"broken". Reproducing it offline is what lets the explanation in the UI be
built and tested without a network, a key, or a second speaker.
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AIGateway, ToolSpec
from app.ai.events import (
    AudioChunkEvent,
    AudioEndEvent,
    AudioStartEvent,
    GatewayEvent,
    TranscriptEvent,
    TurnCompleteEvent,
)

_SAMPLE_RATE = 24_000  # Hz, matches PROTOCOL.md OUTPUT_AUDIO
_TONE_HZ = 330.0
_CHUNK_MS = 20
_TURN_CHUNKS = 5  # ~100 ms of audio

#: A tiny scripted conversation, cycled so a manual run looks alive while
#: staying perfectly deterministic for tests. ``(heard_lang, heard, translated)``
#: — a ``None`` translation means "this was already the target language".
_SCRIPT: tuple[tuple[str, str, str | None], ...] = (
    ("en", "Thanks everyone for coming today.", "आज आने के लिए सबका शुक्रिया।"),
    ("en", "We'll start with the budget.", "हम बजट से शुरू करेंगे।"),
    # Already in the target language → the model stays silent.
    ("hi", "ठीक है, मैं कल तक भेज देता हूँ।", None),
)


def _sine_chunk(start_sample: int, n_samples: int) -> bytes:
    """Generate ``n_samples`` of low-amplitude PCM16 sine starting at a phase."""
    out = bytearray()
    amp = 3000  # well below int16 max (32767)
    for i in range(n_samples):
        t = (start_sample + i) / _SAMPLE_RATE
        sample = int(amp * math.sin(2.0 * math.pi * _TONE_HZ * t))
        out += struct.pack("<h", sample)
    return bytes(out)


class MockTranslateGateway(AIGateway):
    """A deterministic in-process translate gateway.

    Mirrors the contract of a real translate adapter: no tools, no system
    prompt, no vision, no text input. ``send_video`` and ``send_text`` are
    accepted and ignored rather than raising, because the session must be able
    to hand this gateway anything a confused client sends without dying.
    """

    provider = "mock_translate"

    def __init__(
        self,
        *,
        target_language: str = "en",
        echo_target_language: bool = False,
        system_prompt: str = "",
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(
            system_prompt=system_prompt,
            tools=tools or [],
            model=model or "mock-translate-1",
        )
        self.target_language = target_language
        self.echo_target_language = echo_target_language
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._connected = False
        self._closed = False
        self._utterance = 0

    async def connect(self) -> None:
        """No-op connect; marks the gateway ready."""
        self._connected = True

    async def _emit(self, event: GatewayEvent) -> None:
        await self._queue.put(event)

    async def _drive_utterance(self) -> None:
        """Produce one deterministic heard→translated utterance."""
        heard_lang, heard, translated = _SCRIPT[self._utterance % len(_SCRIPT)]
        self._utterance += 1

        # What was heard: a partial, then the final. The real model streams
        # exactly this shape, and the UI firms up muted text on the final.
        half = heard[: max(1, len(heard) // 2)]
        await self._emit(
            TranscriptEvent(role="user", text=half, final=False, lang=heard_lang)
        )
        await self._emit(
            TranscriptEvent(role="user", text=heard, final=True, lang=heard_lang)
        )

        # Already the target language: stay silent unless echo is on. No
        # assistant transcript and no audio — the client is expected to explain
        # the silence rather than look broken.
        if translated is None and not self.echo_target_language:
            await self._emit(TurnCompleteEvent())
            return

        spoken = heard if translated is None else translated
        await self._emit(
            TranscriptEvent(
                role="assistant",
                text=spoken,
                final=True,
                lang=self.target_language,
            )
        )
        await self._emit(AudioStartEvent())
        n_per_chunk = int(_SAMPLE_RATE * _CHUNK_MS / 1000)
        for c in range(_TURN_CHUNKS):
            await self._emit(
                AudioChunkEvent(pcm=_sine_chunk(c * n_per_chunk, n_per_chunk))
            )
        await self._emit(AudioEndEvent())
        await self._emit(TurnCompleteEvent())

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        """Treat each non-empty chunk as one utterance to translate."""
        if pcm:
            await self._drive_utterance()

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        """Ignored: the translate path never forwards vision."""
        return None

    async def send_text(self, text: str) -> None:
        """Ignored: the translate model takes no text input."""
        return None

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        """Ignored: the translate path exposes no tools."""
        return None

    async def events(self) -> AsyncIterator[GatewayEvent]:
        """Yield queued events until :meth:`close` enqueues the sentinel."""
        while True:
            event = await self._queue.get()
            if event is None:  # sentinel
                return
            yield event

    async def interrupt(self) -> None:
        """Drop any queued output to emulate barge-in."""
        try:
            while True:
                self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    async def close(self) -> None:
        """Stop the event stream (idempotent)."""
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)
