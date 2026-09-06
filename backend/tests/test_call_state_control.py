"""The session learns when a phone call starts and ends.

The assistant cannot see the phone's audio state. Device-observed 2026-09-06:
it placed a call, was told "calling", and then kept saying the call was still
in progress long after it had ended — because nothing ever told it otherwise.
The client now reports the transition and the session turns it into a silent
note, which lands in the model's context without making it announce anything.
"""

from __future__ import annotations

import pytest

from app.agent.tool_engine import ToolEngine
from app.config import get_settings
from app.ws.session import Session

pytestmark = pytest.mark.asyncio


class _NotingGateway:
    """Records the silent notes a session pushes into the model context."""

    def __init__(self) -> None:
        self.notes: list[str] = []

    async def send_silent_note(self, text: str) -> None:
        self.notes.append(text)


def _session(gateway) -> Session:
    session = Session(
        object(),  # websocket — untouched on this path
        gateway_factory=lambda _p, _s: gateway,
        engine=ToolEngine.from_tools([]),
        settings=get_settings(),
    )
    session._gateway = gateway
    return session


async def test_a_call_ending_is_put_in_front_of_the_model() -> None:
    gateway = _NotingGateway()
    session = _session(gateway)

    await session._dispatch_control({"type": "call_state", "inCall": False})

    assert len(gateway.notes) == 1
    note = gateway.notes[0].lower()
    assert "ended" in note
    # The exact failure this exists to stop.
    assert "do not say a call is in progress" in note


async def test_a_call_starting_says_the_user_cannot_talk() -> None:
    gateway = _NotingGateway()
    session = _session(gateway)

    await session._dispatch_control({"type": "call_state", "inCall": True})

    assert "in progress" in gateway.notes[0].lower()
    assert "cannot talk" in gateway.notes[0].lower()


async def test_a_missing_flag_reads_as_the_call_being_over() -> None:
    """A malformed message must not leave the model believing it is calling."""
    gateway = _NotingGateway()
    session = _session(gateway)

    await session._dispatch_control({"type": "call_state"})

    assert "ended" in gateway.notes[0].lower()


async def test_a_gateway_without_notes_is_not_a_crash() -> None:
    """Not every provider supports silent notes; the session must not care."""

    class _Bare:
        pass

    session = _session(_Bare())
    await session._dispatch_control({"type": "call_state", "inCall": False})


async def test_a_call_counts_as_activity() -> None:
    """A call is a person using the phone — the idle cap must not fire mid-call."""
    gateway = _NotingGateway()
    session = _session(gateway)
    session._last_activity = 0.0

    await session._dispatch_control({"type": "call_state", "inCall": True})

    assert session._last_activity > 0.0
