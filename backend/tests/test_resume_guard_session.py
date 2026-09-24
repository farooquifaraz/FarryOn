"""The end_session guard after a resumed connect is really armed.

c19df6a added it (device 2026-09-13: two fresh sessions ended themselves
10 s in, re-running the previous conversation's "end the session"), but the
session set it before the orchestrator existed, so it never applied. And the
turn counter it relies on only moved on TYPED turns, so once armed it would
have refused a spoken "end the session" for 30 s.
"""

from __future__ import annotations

import time

import pytest

from app.agent.tool_engine import ToolEngine
from app.ai.events import ToolCallEvent
from app.config import get_settings
from app.ws.session import Session

pytestmark = pytest.mark.asyncio


class _Gateway:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, bool]] = []

    def set_camera_kind(self, kind) -> None:
        self.kind = kind

    async def send_tool_result(self, call_id, name, result, ok=True) -> None:
        self.results.append((call_id, name, ok))


def _session(*, resumed: bool) -> Session:
    s = Session(
        object(),
        gateway_factory=lambda *a: None,
        engine=ToolEngine.from_tools([]),
        settings=get_settings(),
    )
    s._gateway = _Gateway()
    s._hello = {"device": {"kind": "phone"}}
    if resumed:
        s._resumed_from_handle = True
    return s


async def test_a_resumed_session_arms_the_guard() -> None:
    s = _session(resumed=True)
    s._create_orchestrator()
    assert s._orchestrator.resume_guard_until > time.monotonic()


async def test_a_fresh_session_does_not() -> None:
    s = _session(resumed=False)
    s._create_orchestrator()
    assert s._orchestrator.resume_guard_until == 0.0


async def test_a_replayed_end_session_is_refused_after_a_resume() -> None:
    s = _session(resumed=True)
    s._create_orchestrator()
    result = await s._orchestrator.handle_tool_call(
        ToolCallEvent(id="c1", name="end_session", args={})
    )
    assert result.ok is False and "resumed" in (result.error or "")


async def test_a_spoken_turn_lifts_the_guard() -> None:
    # A voice turn reaches the orchestrator only through note_user_turn
    # (the first partial transcript); it must count as heard.
    s = _session(resumed=True)
    s._create_orchestrator()
    s._orchestrator.note_user_turn()
    assert s._orchestrator.user_turns_heard == 1
    assert not (
        s._orchestrator.user_turns_heard == 0
        and time.monotonic() < s._orchestrator.resume_guard_until
    )
