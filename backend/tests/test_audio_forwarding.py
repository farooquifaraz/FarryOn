"""The WebSocket reader must not wait for slow upstream audio sends."""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.ws.frames import FrameTag, encode_frame
from app.ws.session import AudioBackpressureError, Session, _AUDIO_FORWARD_QUEUE_MAX

pytestmark = pytest.mark.asyncio


class _SlowGateway:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.audio: list[bytes] = []

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        self.started.set()
        await self.release.wait()
        self.audio.append(pcm)


def _frame(payload: bytes) -> bytes:
    return encode_frame(FrameTag.INPUT_AUDIO, payload, 0)


async def test_slow_provider_send_does_not_block_the_next_input_frame() -> None:
    session = Session(
        object(),
        gateway_factory=lambda *args: None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        settings=Settings(refine_user_transcripts=False),
    )
    gateway = _SlowGateway()
    session._gateway = gateway  # type: ignore[assignment]
    session._audio_sender_task = asyncio.create_task(session._audio_sender())

    try:
        await asyncio.wait_for(session._handle_binary(_frame(b"first")), 0.1)
        await gateway.started.wait()
        await asyncio.wait_for(session._handle_binary(_frame(b"second")), 0.1)
        assert session._audio_queue.qsize() == 1

        gateway.release.set()
        await session._audio_queue.join()
        assert gateway.audio == [b"first", b"second"]
    finally:
        session._audio_sender_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session._audio_sender_task


async def test_audio_backpressure_is_reported_and_not_silently_dropped() -> None:
    session = Session(
        object(),
        gateway_factory=lambda *args: None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        settings=Settings(refine_user_transcripts=False),
    )
    gateway = _SlowGateway()
    session._gateway = gateway  # type: ignore[assignment]
    session._audio_sender_task = asyncio.create_task(session._audio_sender())
    errors: list[tuple[str, bool]] = []

    async def record_error(code: str, message: str, *, fatal: bool = False) -> None:
        errors.append((code, fatal))

    session._send_error = record_error  # type: ignore[method-assign]
    try:
        await session._queue_audio(b"in-flight", 0)
        await gateway.started.wait()
        for _ in range(_AUDIO_FORWARD_QUEUE_MAX):
            await session._queue_audio(b"queued", 0)

        with pytest.raises(AudioBackpressureError):
            await session._queue_audio(b"must-not-disappear", 0)
        assert errors == [("audio_backpressure", True)]
    finally:
        session._audio_sender_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session._audio_sender_task
