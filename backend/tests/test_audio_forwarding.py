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


async def test_audio_backpressure_drops_the_oldest_frame_and_keeps_the_session() -> None:
    """A provider that stops taking audio for a moment costs 40 ms of stale
    mic audio, not the conversation.

    It used to raise and end the session with a fatal error — twice on
    2026-09-13 mid-conversation, each time right after the model had gone
    quiet on glasses audio. The newest frame is the one still worth hearing,
    so the OLDEST goes, the count is kept, and nothing is sent to the client.
    """
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
        for i in range(_AUDIO_FORWARD_QUEUE_MAX):
            await session._queue_audio(b"queued-%d" % i, 0)

        await session._queue_audio(b"newest", 0)  # no raise
        assert session._audio_dropped == 1
        assert session._audio_queue.qsize() == _AUDIO_FORWARD_QUEUE_MAX
        assert errors == [], "backpressure is not the client's problem"
        # The oldest queued frame went; the newest is at the back.
        items = list(session._audio_queue._queue)  # type: ignore[attr-defined]
        assert items[0][0] == b"queued-1"
        assert items[-1][0] == b"newest"
        assert AudioBackpressureError  # the type stays importable
    finally:
        session._audio_sender_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session._audio_sender_task
