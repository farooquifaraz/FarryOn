"""A picture that already arrived is the answer to the question that asked for it.

`wait_for_frame` backs `identify_image` and `capture_photo`: the tool blocks
until the device sends a frame, so the model has something to look at before it
answers. It only ever heard about the NEXT frame, which loses the race the
moment a device is quick — the capture lands while the tool call is still being
routed, nothing wakes the waiter, and the tool sits out its entire timeout with
the picture sitting in `last_frame` the whole time.

Measured end to end on 2026-08-16: the phone captured at 12:53:10.698 and closed
its camera at .161; the tool began waiting at .267 and gave up 9.4 seconds later,
answering blind. Making the phone take single frames on demand is what made this
reachable — before that a frame arrived every second, so the next one was never
far away and the race was invisible.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agent.orchestrator import RECENT_FRAME_SECONDS, Orchestrator

pytestmark = pytest.mark.asyncio


def _orchestrator() -> Orchestrator:
    async def notify(msg):  # pragma: no cover - not exercised here
        pass

    return Orchestrator(
        engine=None,  # type: ignore[arg-type]
        gateway=None,  # type: ignore[arg-type]
        sessionmaker=None,  # type: ignore[arg-type]
        notify_client=notify,
    )


async def test_a_frame_that_just_landed_answers_immediately():
    orch = _orchestrator()
    orch.last_frame = b"\xff\xd8jpeg"
    orch.last_frame_at = time.monotonic()

    started = time.monotonic()
    got = await orch.wait_for_frame(timeout=5)
    took = time.monotonic() - started

    assert got is True
    assert took < 0.5, "it must not wait for a frame it already has"


async def test_a_frame_from_before_the_question_does_not_count():
    # The other half. Left wide enough and any old picture becomes the answer
    # to whatever is asked next — the model describing something the camera was
    # pointed at a minute ago is worse than it saying it could not see.
    orch = _orchestrator()
    orch.last_frame = b"\xff\xd8old"
    orch.last_frame_at = time.monotonic() - (RECENT_FRAME_SECONDS + 1)

    assert await orch.wait_for_frame(timeout=0.2) is False


async def test_with_no_frame_at_all_it_still_waits_then_times_out():
    orch = _orchestrator()
    assert orch.last_frame is None

    assert await orch.wait_for_frame(timeout=0.2) is False


async def test_a_frame_arriving_during_the_wait_still_wakes_it():
    # The original path, which must keep working: nothing cached, the tool
    # waits, and the device delivers.
    orch = _orchestrator()

    async def deliver():
        await asyncio.sleep(0.05)
        orch.last_frame = b"\xff\xd8fresh"
        orch.last_frame_at = time.monotonic()
        orch.notify_new_frame()

    asyncio.create_task(deliver())
    assert await orch.wait_for_frame(timeout=2) is True
