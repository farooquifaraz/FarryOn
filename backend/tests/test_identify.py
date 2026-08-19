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


async def test_a_read_question_no_longer_goes_to_a_second_model(
    db_session, monkeypatch
) -> None:
    """Reading the view is answered by the model already looking at it.

    This used to hand the question to `run_detection` and wait. Both ends are
    Gemini 2.5 Flash — the live session runs the native-audio variant, the
    detector runs the plain one — so the second call was the same model family
    being asked the same thing about the same picture, one network round trip
    later. It cost 4.4 s of silence on every question and bought nothing.

    Identification still goes the long way; see the test below.
    """
    called = False

    async def fake_run(mode, *, settings, image_data=None, image_url=None,
                       question=None):  # pragma: no cover - must not run
        nonlocal called
        called = True
        return {"ok": True, "mode": "answer", "result": {"answer": "8:20"}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = _fresh_frame_ctx(db_session, b"x")
    res = await IdentifyImageTool().run(
        ctx, question="what time does the clock show?"
    )
    assert res["ok"] is True
    assert called is False
    assert "already in your context" in res["_instruction"]


async def test_landmark_offers_to_send_location(db_session, monkeypatch) -> None:
    """A landmark with a Maps link gets an instruction to offer WhatsApp/Telegram."""

    async def fake_run(mode, *, settings, image_data=None, image_url=None, question=None):
        return {
            "ok": True,
            "mode": "landmark",
            "result": {
                "count": 1,
                "landmarks": [
                    {"name": "Eiffel Tower", "maps_url": "https://maps.google/x"}
                ],
                "source": "Google Vision API",
            },
        }

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = _fresh_frame_ctx(db_session, b"x")
    res = await IdentifyImageTool().run(ctx, kind="landmark")
    assert res["ok"] is True
    instr = res.get("_instruction", "")
    assert "WhatsApp or Telegram" in instr
    assert "https://maps.google/x" in instr  # the exact location link to send


async def test_product_has_no_send_location_instruction(db_session, monkeypatch) -> None:
    """A product result is NOT given the send-location offer (places only)."""

    async def fake_run(mode, *, settings, image_data=None, image_url=None, question=None):
        return {"ok": True, "mode": "product", "result": {"product_name": "Mug"}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = _fresh_frame_ctx(db_session, b"x")
    res = await IdentifyImageTool().run(ctx, kind="auto")
    assert "_instruction" not in res


async def test_invalid_kind_coerces_to_auto(db_session, monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None, question=None):
        seen["mode"] = mode
        return {"ok": True, "mode": "landmark", "result": {"count": 0, "landmarks": []}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = _fresh_frame_ctx(db_session, b"x")
    await IdentifyImageTool().run(ctx, kind="garbage")
    assert seen["mode"] == "auto"


async def test_a_question_is_answered_by_the_model_that_already_sees_it(
    db_session, monkeypatch
) -> None:
    """A question about the view must not wait on a second vision model.

    The frame reaches the live model on its way into this tool — that is what
    the wait is for — so it is already looking at the picture. Sending the
    same picture to a separate vision model and waiting for that answer added
    4.4 seconds to every "what is this?" (measured 2026-08-19: the frame was
    in hand at 1.6 s and the tool returned at 6.0 s). Six seconds of silence
    reads as a camera that never fired, which is exactly what was reported.
    """
    called = False

    async def _must_not_run(*args, **kwargs):  # pragma: no cover - the point
        nonlocal called
        called = True
        return {"ok": True, "mode": "landmark"}

    monkeypatch.setattr(identify_mod, "run_detection", _must_not_run)

    ctx = _fresh_frame_ctx(db_session, b"\xff\xd8jpeg\xff\xd9")
    result = await IdentifyImageTool().run(ctx, question="who is sitting here?")

    assert result["ok"] is True
    assert called is False, "no second vision call for a question about the view"
    assert "already in your context" in result["_instruction"]


async def test_identification_still_goes_the_long_way(db_session, monkeypatch) -> None:
    """The other half. Landmark and product lookups are what produce the Maps
    and shopping links, and those cannot come from the live model's own eyes —
    so with no question, the vision call must still happen.
    """
    called = False

    async def _detect(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True, "mode": "product", "products": []}

    monkeypatch.setattr(identify_mod, "run_detection", _detect)

    ctx = _fresh_frame_ctx(db_session, b"\xff\xd8jpeg\xff\xd9")
    result = await IdentifyImageTool().run(ctx, kind="product")

    assert called is True, "identification still needs the detector"
    assert result["mode"] == "product"
