"""Unit tests for the P0-1 camera-frame cost gate in :class:`Session`.

Every video frame the session forwards is re-billed on every later Live-API
turn, so streaming ~1 fps is the biggest cost driver. These tests pin the
gating behaviour of ``vision_frame_mode`` without any network or provider —
including the rule that a frame a tool is WAITING for (capture_photo) is never
dropped, or the model would answer blind.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.ws.session as session_mod
from app.config import Settings
from app.ws.frames import FrameTag, encode_frame
from app.ws.session import Session

pytestmark = pytest.mark.asyncio


class _FakeGateway:
    def __init__(self) -> None:
        self.videos: list[bytes] = []
        self.audios: list[bytes] = []

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        self.videos.append(jpeg)

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        self.audios.append(pcm)


def _make_session(
    settings: Settings, *, awaiting: bool = False
) -> tuple[Session, _FakeGateway]:
    sess = Session(
        object(),  # websocket — untouched by _handle_binary
        gateway_factory=lambda *a: None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        settings=settings,
    )
    gw = _FakeGateway()
    sess._gateway = gw  # type: ignore[assignment]
    sess._orchestrator = SimpleNamespace(
        last_frame=None,
        last_frame_at=None,
        is_awaiting_frame=lambda: awaiting,
        notify_new_frame=lambda: None,
    )
    return sess, gw


def _clock(monkeypatch, values: list[float]) -> None:
    """Make time.monotonic() return successive values from the list."""
    it = iter(values)
    last = [values[0]]

    def fake() -> float:
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]

    monkeypatch.setattr(session_mod.time, "monotonic", fake)


def _video(payload: bytes = b"jpeg") -> bytes:
    return encode_frame(FrameTag.INPUT_VIDEO, payload, 0)


async def test_off_mode_never_forwards_but_caches(monkeypatch):
    settings = Settings(vision_frame_mode="off")
    sess, gw = _make_session(settings)
    _clock(monkeypatch, [1000.0, 1001.0, 1050.0])
    for _ in range(3):
        await sess._handle_binary(_video())
    assert gw.videos == []                            # nothing streamed
    assert sess._orchestrator.last_frame == b"jpeg"   # cached for identify_image


async def test_continuous_mode_throttles_to_min_interval(monkeypatch):
    settings = Settings(
        vision_frame_mode="continuous", vision_frame_min_interval_s=2.0
    )
    sess, gw = _make_session(settings)
    # t=1000 (send), 1000.5 (skip <2s), 1003 (send)
    _clock(monkeypatch, [1000.0, 1000.0, 1000.5, 1003.0, 1003.0])
    for _ in range(3):
        await sess._handle_binary(_video())
    assert len(gw.videos) == 2


async def test_on_turn_mode_uses_heartbeat_interval(monkeypatch):
    settings = Settings(
        vision_frame_mode="on_turn",
        vision_frame_min_interval_s=2.0,   # must be IGNORED in on_turn
        vision_frame_heartbeat_s=6.0,
    )
    sess, gw = _make_session(settings)
    # t=1000 (send), 1002 (skip: <6s though >2s), 1007 (send)
    _clock(monkeypatch, [1000.0, 1000.0, 1002.0, 1007.0, 1007.0])
    for _ in range(3):
        await sess._handle_binary(_video())
    assert len(gw.videos) == 2   # heartbeat, not the 2s min-interval


async def test_awaited_frame_bypasses_the_gate(monkeypatch):
    """capture_photo's one-shot MUST reach the model even in 'off' mode —
    otherwise the model describes a scene it never saw."""
    settings = Settings(vision_frame_mode="off")
    sess, gw = _make_session(settings, awaiting=True)
    _clock(monkeypatch, [1000.0, 1000.0])
    await sess._handle_binary(_video(b"snap"))
    assert gw.videos == [b"snap"]


async def test_frame_counters_track_received_and_sent(monkeypatch):
    """The gate keeps an auditable count: every inbound frame is counted, only
    the forwarded ones bump sent — this is what the log line reports."""
    settings = Settings(
        vision_frame_mode="continuous", vision_frame_min_interval_s=2.0
    )
    sess, gw = _make_session(settings)
    _clock(monkeypatch, [1000.0, 1000.0, 1000.5, 1003.0, 1003.0])
    for _ in range(3):
        await sess._handle_binary(_video())
    assert sess._frames_in_video == 3      # all received
    assert sess._frames_sent_video == 2    # gate forwarded 2 (logged each)


async def test_typed_turn_attaches_fresh_frame(monkeypatch):
    settings = Settings(vision_frame_mode="off")  # even with streaming off
    sess, gw = _make_session(settings)
    sess._orchestrator.last_frame = b"cam"
    sess._orchestrator.last_frame_at = 1000.0
    _clock(monkeypatch, [1000.0, 1000.0])
    await sess._attach_frame_if_fresh()
    assert gw.videos == [b"cam"]


async def test_typed_turn_skips_stale_frame(monkeypatch):
    settings = Settings(vision_frame_mode="off")
    sess, gw = _make_session(settings)
    sess._orchestrator.last_frame = b"cam"
    sess._orchestrator.last_frame_at = 1000.0
    _clock(monkeypatch, [1020.0])   # 20s later — camera likely off
    await sess._attach_frame_if_fresh()
    assert gw.videos == []
