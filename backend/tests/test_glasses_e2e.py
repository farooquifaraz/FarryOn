"""Glasses hardware-flow simulation (server-side, no network, no glasses).

These tests can't hold real smart glasses, so they reproduce the exact server
behaviour that a glasses session triggers, driving the REAL
:class:`~app.agent.orchestrator.Orchestrator`, :class:`ToolEngine`, and the
``identify_image`` / ``capture_photo`` tools:

- a photo-trigger glasses still lands SEVERAL SECONDS after the tool starts (BLE
  transfer while A2DP audio contends for the radio) — the tool must wait for it
  and accept it, not reject it as stale,
- a device ``capture_failed`` must wake the waiting tool immediately with the
  precise reason instead of hanging for the whole budget,
- connecting glasses mid-session (``device_update``) must widen BOTH the
  orchestrator's frame-wait budget and the gateway's frame-freshness window.

The only thing mocked is ``run_detection`` (the outbound Vision/Gemini call);
everything else is the production code path.
"""

from __future__ import annotations

import asyncio
import base64
import time

import pytest

from app.agent.orchestrator import Orchestrator
from app.agent.tool_engine import ToolEngine
from app.ai.events import ToolCallEvent
from app.ai.mock import MockGateway
from app.config import get_settings
from app.db import base as db_base
from app.tools import build_default_tools
from app.tools import device as device_mod
from app.tools import identify as identify_mod
from app.tools.capture_feedback import capture_failure_message

pytestmark = pytest.mark.asyncio

_GLASSES_BUDGET = 18.0


def _orchestrator() -> Orchestrator:
    """A real orchestrator wired to a mock gateway and the full tool set."""
    engine = ToolEngine.from_tools(build_default_tools())
    gateway = MockGateway(system_prompt="s", tools=[])

    async def _notify(_payload: dict) -> None:
        return None

    return Orchestrator(
        engine=engine,
        gateway=gateway,
        sessionmaker=db_base.get_sessionmaker(),
        notify_client=_notify,
        session_id="sess-glasses",
        frame_wait_seconds=_GLASSES_BUDGET,
    )


async def _await_parked_waiter(orch: Orchestrator) -> None:
    """Wait until the running tool has parked its frame-waiter.

    Avoids a race: we must deliver the simulated photo only AFTER the tool is
    actually blocked in ``wait_for_frame`` (else notify finds no waiter).
    """
    for _ in range(200):
        if orch._frame_waiters:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("tool never parked a frame-waiter")


async def test_slow_glasses_photo_is_accepted_by_identify(monkeypatch) -> None:
    """identify_image waits for a late glasses still and detects on it."""
    seen: dict[str, object] = {}

    async def fake_run(mode, *, settings, image_data=None, image_url=None, question=None):
        seen["mode"] = mode
        seen["image_data"] = image_data
        return {
            "ok": True,
            "mode": "product",
            "result": {"product_name": "Sony WH-1000XM5"},
        }

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)

    orch = _orchestrator()
    call = ToolCallEvent(id="c1", name="identify_image", args={"kind": "auto"})
    task = asyncio.create_task(orch.handle_tool_call(call))

    # The shutter + BLE transfer takes seconds; deliver the photo late, EXACTLY
    # as the session does when an INPUT_VIDEO frame arrives.
    await _await_parked_waiter(orch)
    orch.last_frame = b"\xff\xd8glasses-photo"
    orch.last_frame_at = time.monotonic()
    orch.notify_new_frame()

    result = await task
    assert result.ok is True
    assert seen["mode"] == "auto"
    assert seen["image_data"] == base64.b64encode(b"\xff\xd8glasses-photo").decode()


async def test_capture_failure_reason_is_surfaced(monkeypatch) -> None:
    """A device capture_failed wakes identify_image with the precise reason."""

    async def fake_run(*args, **kwargs):  # must never run on a failed capture
        raise AssertionError("detection must not run when capture failed")

    monkeypatch.setattr(identify_mod, "run_detection", fake_run)

    orch = _orchestrator()
    call = ToolCallEvent(id="c2", name="identify_image", args={})
    task = asyncio.create_task(orch.handle_tool_call(call))

    await _await_parked_waiter(orch)
    orch.notify_capture_failed("not_connected")  # a PROTOCOL.md wire reason code

    result = await task
    # The tool call itself succeeds (no exception); the tool's PAYLOAD reports
    # the failure so the model can speak it.
    assert result.ok is True
    assert result.result["ok"] is False
    # The user hears the reason-specific line, not a generic timeout.
    assert result.result["error"] == capture_failure_message("not_connected")
    assert "glasses aren't connected" in result.result["error"]


async def test_capture_photo_describes_slow_glasses_frame(monkeypatch) -> None:
    """capture_photo waits for the still, then describes the ACTUAL frame."""

    async def fake_run(mode, *, settings, image_data=None, image_url=None, question=None):
        assert question, "capture_photo must use the server-side describe path"
        assert image_data == base64.b64encode(b"\xff\xd8room").decode()
        return {"ok": True, "mode": "answer", "result": {"answer": "A desk with a laptop."}}

    monkeypatch.setattr(device_mod, "run_detection", fake_run)

    orch = _orchestrator()
    call = ToolCallEvent(id="c3", name="capture_photo", args={})
    task = asyncio.create_task(orch.handle_tool_call(call))

    await _await_parked_waiter(orch)
    orch.last_frame = b"\xff\xd8room"
    orch.last_frame_at = time.monotonic()
    orch.notify_new_frame()

    result = await task
    assert result.ok is True
    assert result.result["captured"] is True
    assert "laptop" in result.result["description"].lower()


async def test_device_update_widens_orchestrator_and_gateway() -> None:
    """Glasses connecting mid-session widens the budget AND the gateway window."""
    from app.ai.openai_realtime import _FRAME_MAX_AGE_SECONDS, OpenAIRealtimeGateway
    from app.ws.session import Session

    gateway = OpenAIRealtimeGateway(system_prompt="s", tools=[])
    assert gateway._frame_max_age == _FRAME_MAX_AGE_SECONDS  # phone default

    settings = get_settings()
    session = Session(
        object(),  # websocket — untouched on the device_update path
        gateway_factory=lambda _p, _s: gateway,
        engine=ToolEngine.from_tools([]),
        settings=settings,
    )
    session._gateway = gateway
    session._orchestrator = _orchestrator()

    await session._dispatch_control(
        {"type": "device_update", "videoKind": "phone+glasses"}
    )

    # Both the wait budget (server-side tool) and the freshness window (gateway
    # frame attach) now reflect the slow glasses camera.
    assert session._orchestrator._frame_wait_seconds == settings.glasses_frame_wait_seconds
    assert gateway._frame_max_age == settings.glasses_frame_wait_seconds
