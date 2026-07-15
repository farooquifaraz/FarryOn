"""Tests for the device→server capture-failure path.

Covers the ``capture_failed`` control message end of the robust glasses
pipeline: the orchestrator's early wake-up with a reason, the reason→message
taxonomy, and how ``capture_photo`` / ``identify_image`` surface it.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agent.orchestrator import Orchestrator
from app.tools.base import ToolContext
from app.tools.capture_feedback import (
    DEFAULT_CAPTURE_FAILURE_MESSAGE,
    capture_failure_message,
)
from app.tools.device import CapturePhotoTool
from app.tools.identify import IdentifyImageTool

pytestmark = pytest.mark.asyncio


def _orchestrator(frame_wait_seconds: float = 5.0) -> Orchestrator:
    async def _noop_notify(_: dict) -> None:  # pragma: no cover - unused
        return None

    return Orchestrator(
        engine=None,  # type: ignore[arg-type]  # not dispatched in these tests
        gateway=None,  # type: ignore[arg-type]
        sessionmaker=None,  # type: ignore[arg-type]
        notify_client=_noop_notify,
        frame_wait_seconds=frame_wait_seconds,
    )


# -- Orchestrator: wait_for_frame / notify_capture_failed ---------------------


async def test_capture_failed_wakes_waiter_early_with_reason() -> None:
    orch = _orchestrator(frame_wait_seconds=5.0)
    waiter = asyncio.create_task(orch.wait_for_frame())
    await asyncio.sleep(0)  # let the waiter park

    started = time.monotonic()
    orch.notify_capture_failed("not_connected")
    got = await waiter

    assert got is False
    assert time.monotonic() - started < 1.0  # woke early, not the full budget
    assert orch.last_capture_error == "not_connected"


async def test_new_frame_wakes_waiter_and_clears_stale_error() -> None:
    orch = _orchestrator()
    orch.last_capture_error = "busy"  # stale report from an earlier attempt
    waiter = asyncio.create_task(orch.wait_for_frame())
    await asyncio.sleep(0)

    orch.notify_new_frame()
    assert await waiter is True
    assert orch.last_capture_error is None


async def test_set_frame_wait_seconds_updates_the_budget() -> None:
    # Glasses connect after hello, so the budget is bumped mid-session via a
    # device_update. A later wait_for_frame must use the NEW budget.
    orch = _orchestrator(frame_wait_seconds=0.05)
    orch.set_frame_wait_seconds(0.30)
    started = time.monotonic()
    assert await orch.wait_for_frame() is False  # times out on the NEW budget
    elapsed = time.monotonic() - started
    assert elapsed >= 0.25  # waited the updated 0.30 s, not the old 0.05 s


async def test_wait_for_frame_times_out_with_injected_budget() -> None:
    orch = _orchestrator(frame_wait_seconds=0.05)
    started = time.monotonic()
    assert await orch.wait_for_frame() is False
    assert time.monotonic() - started < 1.0


# -- Reason taxonomy -----------------------------------------------------------


async def test_every_wire_reason_has_a_message() -> None:
    for reason in (
        "not_connected",
        "busy",
        "capture_timeout",
        "transfer_stalled",
        "empty_image",
        "command_failed",
    ):
        assert capture_failure_message(reason) != DEFAULT_CAPTURE_FAILURE_MESSAGE


async def test_unknown_or_missing_reason_falls_back_to_default() -> None:
    assert capture_failure_message(None) == DEFAULT_CAPTURE_FAILURE_MESSAGE
    assert capture_failure_message("garbage") == DEFAULT_CAPTURE_FAILURE_MESSAGE


# -- Tools surface the device's reason ----------------------------------------


def _failed_capture_ctx(db_session, reason: str) -> ToolContext:
    """Context simulating a device that reported ``capture_failed``."""

    async def wake_with_failure(timeout: float | None = None) -> bool:
        return False

    return ToolContext(
        session=db_session,
        wait_for_frame=wake_with_failure,
        capture_error=lambda: reason,
    )


async def test_capture_photo_speaks_the_device_reason(db_session) -> None:
    res = await CapturePhotoTool().run(_failed_capture_ctx(db_session, "busy"))
    assert res["captured"] is False
    assert res["_instruction"] == capture_failure_message("busy")


async def test_identify_image_speaks_the_device_reason(db_session) -> None:
    res = await IdentifyImageTool().run(
        _failed_capture_ctx(db_session, "not_connected")
    )
    assert res["ok"] is False
    assert res["error"] == capture_failure_message("not_connected")
