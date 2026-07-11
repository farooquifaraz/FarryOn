"""Tests for the identify_image tool (camera-frame freshness + dispatch)."""

from __future__ import annotations

import base64
import time

import pytest

from app.tools import identify as identify_mod
from app.tools.base import ToolContext
from app.tools.identify import IdentifyImageTool

pytestmark = pytest.mark.asyncio


def _fresh_frame_ctx(db_session, jpeg: bytes) -> ToolContext:
    """Context mirroring the real live flow EXACTLY: the app snaps a photo
    when it sees the tool_call, the frame lands on the ORCHESTRATOR while the
    tool awaits ``wait_for_frame``, and the tool must read it through the
    live ``latest_frame`` accessor. The dispatch-time snapshot fields stay
    ``None`` on purpose — a tool that consults the snapshot instead of the
    accessor must fail here (that exact bug shipped: 2026-07-11 a delivered
    glasses photo was rejected as "not fresh").
    """
    live: dict[str, object] = {"frame": None, "at": None}

    async def deliver_frame(timeout: float | None = None) -> bool:
        live["frame"] = jpeg
        live["at"] = time.monotonic()
        return True

    return ToolContext(
        session=db_session,
        wait_for_frame=deliver_frame,
        latest_frame=lambda: (live["frame"], live["at"]),  # type: ignore[return-value]
    )


async def test_no_frame_returns_friendly_error(db_session) -> None:
    res = await IdentifyImageTool().run(ToolContext(session=db_session))
    assert res["ok"] is False
    assert res["error"]


async def test_stale_frame_is_rejected(db_session) -> None:
    """A frame older than the stale window is treated as no current frame."""
    ctx = ToolContext(
        session=db_session,
        last_frame=b"jpegbytes",
        last_frame_at=time.monotonic() - 30,
    )
    res = await IdentifyImageTool().run(ctx)
    assert res["ok"] is False


async def test_fresh_frame_dispatches_to_detection(db_session, monkeypatch) -> None:
    """A fresh frame is base64-encoded and passed to run_detection."""
    seen: dict[str, object] = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None, question=None):
        seen["mode"] = mode
        seen["image_data"] = image_data
        return {"ok": True, "mode": "landmark", "result": {"count": 0, "landmarks": []}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)

    ctx = _fresh_frame_ctx(db_session, b"hello")
    res = await IdentifyImageTool().run(ctx, kind="landmark")
    assert res["ok"] is True
    assert seen["mode"] == "landmark"
    assert seen["image_data"] == base64.b64encode(b"hello").decode("utf-8")


async def test_question_is_passed_through(db_session, monkeypatch) -> None:
    """A read/answer question reaches run_detection (the read path)."""
    seen: dict[str, object] = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None,
                       question=None):
        seen["question"] = question
        return {"ok": True, "mode": "answer", "result": {"answer": "8:20"}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = _fresh_frame_ctx(db_session, b"x")
    res = await IdentifyImageTool().run(
        ctx, question="what time does the clock show?"
    )
    assert res["ok"] is True
    assert seen["question"] == "what time does the clock show?"


async def test_invalid_kind_coerces_to_auto(db_session, monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None, question=None):
        seen["mode"] = mode
        return {"ok": True, "mode": "landmark", "result": {"count": 0, "landmarks": []}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = _fresh_frame_ctx(db_session, b"x")
    await IdentifyImageTool().run(ctx, kind="garbage")
    assert seen["mode"] == "auto"
