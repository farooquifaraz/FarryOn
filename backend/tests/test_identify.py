"""Tests for the identify_image tool (camera-frame freshness + dispatch)."""

from __future__ import annotations

import base64
import time

import pytest

from app.tools import identify as identify_mod
from app.tools.base import ToolContext
from app.tools.identify import IdentifyImageTool

pytestmark = pytest.mark.asyncio


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

    async def fake_run(mode, *, settings, image_data=None, image_url=None):
        seen["mode"] = mode
        seen["image_data"] = image_data
        return {"ok": True, "mode": "landmark", "result": {"count": 0, "landmarks": []}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)

    ctx = ToolContext(
        session=db_session,
        last_frame=b"hello",
        last_frame_at=time.monotonic(),
    )
    res = await IdentifyImageTool().run(ctx, kind="landmark")
    assert res["ok"] is True
    assert seen["mode"] == "landmark"
    assert seen["image_data"] == base64.b64encode(b"hello").decode("utf-8")


async def test_invalid_kind_coerces_to_auto(db_session, monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None):
        seen["mode"] = mode
        return {"ok": True, "mode": "landmark", "result": {"count": 0, "landmarks": []}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = ToolContext(
        session=db_session, last_frame=b"x", last_frame_at=time.monotonic()
    )
    await IdentifyImageTool().run(ctx, kind="garbage")
    assert seen["mode"] == "auto"
