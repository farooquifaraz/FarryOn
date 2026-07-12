"""Frame-freshness window on the OpenAI Realtime adapter.

The OpenAI adapter can't stream continuous video, so it caches the latest camera
frame and attaches it to the conversation when the user takes a turn. Photo-
trigger smart glasses deliver a still several seconds after the shutter, so the
freshness window must widen for glasses — otherwise the (legitimate but slow)
photo is dropped and the model answers blind, unlike Gemini which streams every
frame. These tests lock that device-aware sizing (no network / no openai SDK).
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from app.ai import openai_realtime
from app.ai.base import ToolSpec
from app.ai.openai_realtime import _FRAME_MAX_AGE_SECONDS, OpenAIRealtimeGateway
from app.config import get_settings


def _gateway() -> OpenAIRealtimeGateway:
    return OpenAIRealtimeGateway(
        system_prompt="sys",
        tools=[ToolSpec(name="t", description="d", parameters={"type": "object"})],
    )


class _RecordingConn:
    """Minimal stand-in for the Realtime connection that records item events."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.deleted: list[str] = []
        self.conversation = self
        self.item = self

    async def create(self, *, item: dict) -> None:
        self.items.append(item)

    async def delete(self, *, item_id: str) -> None:
        self.deleted.append(item_id)


def test_default_window_is_phone_streaming() -> None:
    gw = _gateway()
    assert gw._frame_max_age == _FRAME_MAX_AGE_SECONDS


def test_set_camera_kind_widens_for_glasses() -> None:
    gw = _gateway()
    glasses_budget = get_settings().glasses_frame_wait_seconds

    gw.set_camera_kind("glasses")
    assert gw._frame_max_age == glasses_budget

    # A combo (phone mic + glasses camera) still counts as glasses.
    gw.set_camera_kind("phone+glasses")
    assert gw._frame_max_age == glasses_budget

    # Switching back to a phone-only camera restores the short window.
    gw.set_camera_kind("phone")
    assert gw._frame_max_age == _FRAME_MAX_AGE_SECONDS

    gw.set_camera_kind(None)
    assert gw._frame_max_age == _FRAME_MAX_AGE_SECONDS


@pytest.mark.asyncio
async def test_fresh_phone_frame_is_attached(monkeypatch) -> None:
    """A fresh phone-camera frame is attached inline (streaming live vision)."""
    now = [1000.0]
    monkeypatch.setattr(openai_realtime.time, "monotonic", lambda: now[0])

    gw = _gateway()
    gw._conn = _RecordingConn()
    await gw.send_video(b"\xff\xd8jpeg-bytes")
    now[0] = 1001.0  # 1 s later — well within the phone window

    await gw._attach_latest_frame()
    assert len(gw._conn.items) == 1
    content = gw._conn.items[0]["content"][0]
    assert content["type"] == "input_image"
    expected_b64 = base64.b64encode(b"\xff\xd8jpeg-bytes").decode("ascii")
    assert expected_b64 in content["image_url"]


@pytest.mark.asyncio
async def test_glasses_frame_is_not_attached_inline(monkeypatch) -> None:
    """In glasses mode the cached frame is NOT auto-attached.

    Glasses don't stream, so auto-attaching the cache replays a STALE scene and
    the model answers about the wrong/old image instead of taking a fresh photo.
    Glasses vision must go through the capture_photo / identify_image tool.
    """
    now = [1000.0]
    monkeypatch.setattr(openai_realtime.time, "monotonic", lambda: now[0])

    gw = _gateway()
    gw._conn = _RecordingConn()
    await gw.send_video(b"\xff\xd8jpeg-bytes")
    now[0] = 1001.0  # fresh by any window

    gw.set_camera_kind("glasses")
    await gw._attach_latest_frame()
    assert gw._conn.items == []  # skipped — no stale frame attached


@pytest.mark.asyncio
async def test_switch_to_glasses_purges_stale_frames(monkeypatch) -> None:
    """Switching to glasses deletes already-attached phone frames + the cache,
    so the model can't keep 'seeing' a stale streamed image from history."""
    now = [1000.0]
    monkeypatch.setattr(openai_realtime.time, "monotonic", lambda: now[0])

    gw = _gateway()
    gw._conn = _RecordingConn()
    # Two phone frames get attached over the session.
    await gw.send_video(b"frame-1")
    await gw._attach_latest_frame()
    now[0] = 1001.0
    await gw.send_video(b"frame-2")
    await gw._attach_latest_frame()
    assert len(gw._conn.items) == 2
    tracked = list(gw._frame_item_ids)
    assert len(tracked) == 2

    # User connects glasses mid-session → purge the stale phone frames + cache.
    gw.set_camera_kind("glasses")
    await asyncio.sleep(0)  # let the scheduled purge task run
    assert gw._latest_frame_b64 is None
    assert set(gw._conn.deleted) == set(tracked)
    assert gw._frame_item_ids == []
