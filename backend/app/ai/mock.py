"""Deterministic, network-free mock gateway.

Used by tests and offline demos. It produces a predictable event stream without
contacting any provider, exercising the full Session/agent/tool path:

- On any input (audio chunk, video frame, or text) it schedules one assistant
  "turn": a streamed assistant transcript, a single :class:`ToolCallEvent` for
  ``create_note``, a short burst of synthetic 24 kHz PCM audio, then
  ``turn_complete``.
- ``send_tool_result`` triggers a brief follow-up assistant transcript so the
  caller can observe the post-tool continuation, mirroring a real provider's
  tool-call loop.
- ``interrupt`` clears any queued/in-flight turn output.

The synthesized audio is a low-amplitude sine wave so downstream code has real
PCM bytes to forward; tests only assert that audio frames are produced.
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
    ToolCallEvent,
    TranscriptEvent,
    TurnCompleteEvent,
)

_SAMPLE_RATE = 24_000  # Hz, matches PROTOCOL.md OUTPUT_AUDIO
_TONE_HZ = 220.0
_CHUNK_MS = 20
_TURN_CHUNKS = 5  # ~100 ms of audio


def _sine_chunk(start_sample: int, n_samples: int) -> bytes:
    """Generate ``n_samples`` of low-amplitude PCM16 sine starting at a phase."""
    out = bytearray()
    amp = 3000  # well below int16 max (32767)
    for i in range(n_samples):
        t = (start_sample + i) / _SAMPLE_RATE
        sample = int(amp * math.sin(2.0 * math.pi * _TONE_HZ * t))
        out += struct.pack("<h", sample)
    return bytes(out)


class MockGateway(AIGateway):
    """A deterministic in-process gateway implementing :class:`AIGateway`."""

    provider = "mock"

    def __init__(
        self,
        *,
        system_prompt: str,
        tools: list[ToolSpec],
        model: str | None = None,
    ) -> None:
        super().__init__(
            system_prompt=system_prompt, tools=tools, model=model or "mock-1"
        )
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._connected = False
        self._closed = False
        self._turn_counter = 0
        self._tool_call_seq = 0

    async def connect(self) -> None:
        """No-op connect; marks the gateway ready."""
        self._connected = True

    async def _emit(self, event: GatewayEvent) -> None:
        await self._queue.put(event)

    async def _drive_turn(self, user_text: str) -> None:
        """Produce one deterministic assistant turn for the given input."""
        self._turn_counter += 1
        # Streaming assistant transcript: partial then final.
        await self._emit(
            TranscriptEvent(role="assistant", text="Sure, ", final=False)
        )
        await self._emit(
            TranscriptEvent(
                role="assistant",
                text="Sure, I'll take a note about that.",
                final=True,
            )
        )

        # One tool call for create_note (canonical tool from PROTOCOL.md).
        self._tool_call_seq += 1
        note_text = user_text.strip() or "Remember this for the user."
        await self._emit(
            ToolCallEvent(
                id=f"mock-call-{self._tool_call_seq}",
                name="create_note",
                args={"text": note_text},
            )
        )

        # Streamed synthetic audio response.
        await self._emit(AudioStartEvent())
        n_per_chunk = int(_SAMPLE_RATE * _CHUNK_MS / 1000)
        for c in range(_TURN_CHUNKS):
            await self._emit(
                AudioChunkEvent(pcm=_sine_chunk(c * n_per_chunk, n_per_chunk))
            )
        await self._emit(AudioEndEvent())
        await self._emit(TurnCompleteEvent())

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        """Treat the first audio chunk of a turn as a spoken request."""
        # Only drive a turn on a reasonably sized chunk to avoid one-per-frame
        # storms; deterministic for tests which send a single chunk.
        if pcm:
            await self._drive_turn("the thing I just said")

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        """Accept a video frame; the mock does not act on vision alone."""
        return None

    async def send_text(self, text: str) -> None:
        """Drive a turn from typed input."""
        await self._drive_turn(text)

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        """Emit a short post-tool assistant confirmation."""
        await self._emit(
            TranscriptEvent(
                role="assistant",
                text="Done — I've saved that note for you.",
                final=True,
            )
        )

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
