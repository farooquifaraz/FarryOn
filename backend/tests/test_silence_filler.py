"""The quiet after a sentence, sent on the client's behalf.

The client's mic gate holds silence back, so the provider's end-of-speech
decision only ever saw the NEXT burst (device-seen 2026-09-11: four short
questions, 27 s unheard). Once per burst, after the gate has gone quiet, the
session sends true silence itself - and stops the moment real audio returns.
"""

from __future__ import annotations

import pytest

from app.agent.tool_engine import ToolEngine
from app.config import Settings
from app.ws.session import Session

pytestmark = pytest.mark.asyncio


class _Gateway:
    def __init__(self):
        self.audio: list[bytes] = []

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None):
        self.audio.append(pcm)


class _S:
    vad_silence_filler_seconds = 0.3  # three 100 ms pieces
    vad_silence_filler_after_ms = 300
    stuck_turn_nudge_seconds = 0.0


def _session(settings=None) -> Session:
    s = Session(
        object(),
        gateway_factory=lambda _p, _s: object(),
        engine=ToolEngine.from_tools([]),
        settings=settings or _S(),
    )
    s._gateway = _Gateway()
    s._mode = "agent"
    return s


async def test_fills_once_after_the_gate_goes_quiet() -> None:
    s = _session()
    s._last_audio_frame_at = 100.0
    assert await s._maybe_fill_silence(100.2) is False, "the gate may still be open"
    assert await s._maybe_fill_silence(100.4) is True
    assert len(s._gateway.audio) == 3
    assert all(len(p) == 3200 and not any(p) for p in s._gateway.audio)
    # The same burst is never filled twice.
    assert await s._maybe_fill_silence(105.0) is False
    assert len(s._gateway.audio) == 3
    # A new burst gets its own fill.
    s._last_audio_frame_at = 110.0
    assert await s._maybe_fill_silence(110.5) is True
    assert len(s._gateway.audio) == 6


async def test_nothing_before_any_audio_and_nothing_when_off() -> None:
    s = _session()
    assert await s._maybe_fill_silence(50.0) is False
    assert s._gateway.audio == []

    class Off(_S):
        vad_silence_filler_seconds = 0.0

    s = _session(Off())
    s._last_audio_frame_at = 1.0
    assert await s._maybe_fill_silence(9.0) is False
    assert s._gateway.audio == []


async def test_translate_mode_is_left_alone() -> None:
    s = _session()
    s._mode = "translate"
    s._last_audio_frame_at = 1.0
    assert await s._maybe_fill_silence(9.0) is False


def test_the_default_is_off() -> None:
    assert Settings.model_fields["vad_silence_filler_seconds"].default == 0.0
