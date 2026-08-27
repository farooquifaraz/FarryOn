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


async def test_a_read_question_is_answered_from_the_fresh_frame(
    db_session, monkeypatch
) -> None:
    """A question about the view is answered by vision on the JUST-captured
    bytes.

    This briefly went the other way ("the live model already sees it") and
    shipped an off-by-one: a frame delivered mid-turn only joins the live
    model's context on the NEXT turn, so every answer described the previous
    photo (device-proven 2026-08-27). The vision call is the fix — it looks
    at exactly the bytes the wait just delivered.
    """
    seen: dict = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None,
                       question=None):
        seen["question"] = question
        seen["image_data"] = image_data
        return {"ok": True, "mode": "answer", "result": {"answer": "8:20"}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = _fresh_frame_ctx(db_session, b"x")
    res = await IdentifyImageTool().run(
        ctx, question="what time does the clock show?"
    )
    assert res["ok"] is True
    assert seen["question"] == "what time does the clock show?"
    assert seen["image_data"]  # the fresh frame, base64'd
    assert res["mode"] == "answer"
    assert res["answer"] == "8:20"
    assert "captured JUST NOW" in res["_instruction"]


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
    # Explicit `product` — that is what still reaches the detector now that
    # `auto` is answered by the live model.
    res = await IdentifyImageTool().run(ctx, kind="product")
    assert "_instruction" not in res


async def test_invalid_kind_coerces_to_auto(db_session, monkeypatch) -> None:
    """A nonsense kind is treated as auto — which asks vision to describe the
    fresh frame with the default what-is-this question."""
    seen: dict = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None,
                       question=None):
        seen["question"] = question
        return {"ok": True, "mode": "answer", "result": {"answer": "a desk"}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)
    ctx = _fresh_frame_ctx(db_session, b"x")
    res = await IdentifyImageTool().run(ctx, kind="garbage")
    assert res["ok"] is True
    assert "What is this" in seen["question"]
    assert res["answer"] == "a desk"


async def test_a_question_reaches_vision_with_the_exact_bytes(
    db_session, monkeypatch
) -> None:
    """The vision call must receive the frame this very wait delivered —
    that is the whole defence against answering about a stale photo."""
    seen: dict = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None,
                       question=None):
        seen["image_data"] = image_data
        return {"ok": True, "mode": "answer", "result": {"answer": "Faraz"}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)

    ctx = _fresh_frame_ctx(db_session, b"jpegbytes")
    result = await IdentifyImageTool().run(ctx, question="who is sitting here?")

    assert result["ok"] is True
    assert seen["image_data"] == base64.b64encode(b"jpegbytes").decode("utf-8")
    assert result["answer"] == "Faraz"


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

    ctx = _fresh_frame_ctx(db_session, b"jpegbytes")
    result = await IdentifyImageTool().run(ctx, kind="product")

    assert called is True, "identification still needs the detector"
    assert result["mode"] == "product"

async def test_auto_goes_to_vision_with_a_describe_question(
    db_session, monkeypatch
) -> None:
    """Plain "what is this?" (kind=auto) is a vision describe over the fresh
    frame. The important assertion is the QUESTION routing — auto must go
    down the answer path, not the slow landmark→product cascade."""
    seen: dict = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None,
                       question=None):
        seen["question"] = question
        return {"ok": True, "mode": "answer", "result": {"answer": "a chair"}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)

    ctx = _fresh_frame_ctx(db_session, b"jpegbytes")
    result = await IdentifyImageTool().run(ctx, kind="auto")

    assert result["ok"] is True
    assert seen["question"], "auto must carry a describe question"
    assert result["answer"] == "a chair"


async def test_no_kind_at_all_is_treated_as_auto(db_session, monkeypatch) -> None:
    # The model often calls this with no arguments whatsoever.
    seen: dict = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None,
                       question=None):
        seen["question"] = question
        return {"ok": True, "mode": "answer", "result": {"answer": "a lamp"}}

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)

    ctx = _fresh_frame_ctx(db_session, b"jpegbytes")
    result = await IdentifyImageTool().run(ctx)

    assert result["ok"] is True
    assert seen["question"]
    assert result["answer"] == "a lamp"
