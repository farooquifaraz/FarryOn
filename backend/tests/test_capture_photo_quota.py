"""``capture_photo`` is metered like ``identify_image`` — once per turn.

The photo is paid work (a vision call on the JPEG), and until now it was the
one image path with no cap. Three things are pinned: the check runs BEFORE
the wait for the frame (a user who waited for the shutter must not then hear
"quota exceeded"); with enforcement off nothing changes; and a turn in which
the model calls both ``capture_photo`` and ``identify_image`` — which it does
for one "what is this?" — costs one scan, not two, while a new turn costs
again.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db import repo
from app.tools import quota
from app.tools.base import ToolContext
from app.tools.device import CapturePhotoTool
from app.tools.identify import IdentifyImageTool

pytestmark = pytest.mark.asyncio


def _enforce(monkeypatch, cap: int) -> None:
    monkeypatch.setattr(
        quota,
        "get_settings",
        lambda: SimpleNamespace(
            quota_enforcement_enabled=True,
            default_plan="free",
            plan_limits={"free": {"image_scans": cap}},
            usage_window=lambda plan: "month",
        ),
    )


def _off(monkeypatch) -> None:
    monkeypatch.setattr(
        quota, "get_settings", lambda: SimpleNamespace(quota_enforcement_enabled=False)
    )


class _Waits:
    """A wait_for_frame that records whether it was ever awaited."""

    def __init__(self, got: bool = True) -> None:
        self.got = got
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        return self.got


async def _scans(db, key: str) -> int:
    return await repo.usage_this_month(
        db, user_key=key, month=quota._this_month(), metric="image_scans"
    )


async def test_a_cap_of_zero_refuses_before_the_frame_is_waited_for(db_session, monkeypatch) -> None:
    _enforce(monkeypatch, cap=0)
    waits = _Waits()
    ctx = ToolContext(session=db_session, session_id="cp-0", wait_for_frame=waits)
    out = await CapturePhotoTool().run(ctx)
    assert out["ok"] is False and out["status"] == "quota_exceeded"
    assert waits.calls == 0, "refused before the user was made to wait for the shutter"


async def test_enforcement_off_leaves_the_tool_exactly_as_it_was(db_session, monkeypatch) -> None:
    _off(monkeypatch)
    waits = _Waits(got=False)  # no frame arrives: the existing failure path
    ctx = ToolContext(session=db_session, session_id="cp-off", wait_for_frame=waits)
    out = await CapturePhotoTool().run(ctx)
    assert waits.calls == 1
    assert out["captured"] is False and "_instruction" in out
    assert await _scans(db_session, "cp-off") == 0


async def test_a_capture_within_the_cap_runs_and_is_recorded(db_session, monkeypatch) -> None:
    _enforce(monkeypatch, cap=5)
    waits = _Waits(got=False)  # frame never lands; the metering happened first
    ctx = ToolContext(session=db_session, session_id="cp-ok", wait_for_frame=waits)
    out = await CapturePhotoTool().run(ctx)
    assert waits.calls == 1 and out["captured"] is False
    assert await _scans(db_session, "cp-ok") == 1


async def test_capture_then_identify_in_one_turn_costs_one_scan(db_session, monkeypatch) -> None:
    _enforce(monkeypatch, cap=5)
    turn: set[str] = set()  # the orchestrator's per-turn slate
    ctx = ToolContext(
        session=db_session,
        session_id="cp-turn",
        turn_charges=turn,
        wait_for_frame=_Waits(got=False),
    )
    await CapturePhotoTool().run(ctx)
    await IdentifyImageTool().run(ctx)  # same turn, same photo: free
    assert await _scans(db_session, "cp-turn") == 1
    # Either order: identify first, then capture, is still one scan.
    turn.clear()
    await IdentifyImageTool().run(ctx)
    await CapturePhotoTool().run(ctx)
    assert await _scans(db_session, "cp-turn") == 2


async def test_a_new_turn_charges_again_and_the_cap_still_bites(db_session, monkeypatch) -> None:
    _enforce(monkeypatch, cap=2)
    turn: set[str] = set()
    ctx = ToolContext(
        session=db_session, session_id="cp-cap", turn_charges=turn, wait_for_frame=_Waits(got=False)
    )
    for _ in range(2):
        turn.clear()  # note_user_turn
        assert (await CapturePhotoTool().run(ctx)).get("status") != "quota_exceeded"
    turn.clear()
    out = await CapturePhotoTool().run(ctx)
    assert out["status"] == "quota_exceeded"
    assert await _scans(db_session, "cp-cap") == 2


async def test_a_refused_scan_does_not_mark_the_turn_as_paid(db_session, monkeypatch) -> None:
    """Refused capture, then identify in the same turn: identify must be
    refused too, not slip through on a charge that never happened."""
    _enforce(monkeypatch, cap=0)
    turn: set[str] = set()
    ctx = ToolContext(session=db_session, session_id="cp-refused", turn_charges=turn)
    assert (await CapturePhotoTool().run(ctx))["status"] == "quota_exceeded"
    assert (await IdentifyImageTool().run(ctx))["status"] == "quota_exceeded"
    assert turn == set()


async def test_the_orchestrator_resets_the_slate_on_a_new_turn_and_a_spoken_reply() -> None:
    from app.agent.orchestrator import Orchestrator

    async def _noop(_: dict) -> None:
        return None

    orch = Orchestrator(
        engine=None,  # type: ignore[arg-type]
        gateway=None,  # type: ignore[arg-type]
        sessionmaker=None,  # type: ignore[arg-type]
        notify_client=_noop,
    )
    orch._turn_charges.add("image_scans")
    orch.note_user_turn()
    assert orch._turn_charges == set()
    orch._turn_charges.add("image_scans")
    orch.note_assistant_spoke()
    assert orch._turn_charges == set()
