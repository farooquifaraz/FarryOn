"""The mailbox choice survives a resumed conversation, not a fresh one.

Device E1.5 (2026-09-12): the user picked the work mailbox, a backend
stuck-reconnect dropped the socket, the app reconnected into the SAME
(resumed) conversation — and the next email request asked "which account?"
again. The model still remembered the choice; the tools had forgotten it.
A fresh session, without resume, must still ask (the 2026-09-08 rule).
"""

from __future__ import annotations

import pytest

import app.ws.session as session_mod
from app.agent.orchestrator import Orchestrator
from app.agent.tool_engine import ToolEngine
from app.config import get_settings
from app.ws.session import Session

pytestmark = pytest.mark.asyncio


def _session(uid: int, *, resumed: bool) -> Session:
    s = Session(
        object(),
        gateway_factory=lambda *a: None,
        engine=ToolEngine.from_tools([]),
        settings=get_settings(),
    )

    async def notify(_: dict) -> None:
        return None

    s._authed_user_id = uid
    s._resumed_from_handle = resumed
    s._orchestrator = Orchestrator(
        engine=ToolEngine.from_tools([]), gateway=None, sessionmaker=None,
        notify_client=notify,
    )
    return s


@pytest.fixture(autouse=True)
def _clean():
    session_mod._EMAIL_SELECTIONS.clear()
    yield
    session_mod._EMAIL_SELECTIONS.clear()


async def test_a_resumed_conversation_keeps_the_mailbox() -> None:
    first = _session(7, resumed=False)
    first._carry_email_selection()
    first._orchestrator.email_selection.update(address="work@example.com", asked=True)

    again = _session(7, resumed=True)
    again._carry_email_selection()
    assert again._orchestrator.email_selection.get("address") == "work@example.com"


async def test_a_fresh_session_asks_again() -> None:
    first = _session(7, resumed=False)
    first._carry_email_selection()
    first._orchestrator.email_selection.update(address="work@example.com", asked=True)

    fresh = _session(7, resumed=False)
    fresh._carry_email_selection()
    assert fresh._orchestrator.email_selection == {}


async def test_another_user_never_inherits_it() -> None:
    first = _session(7, resumed=False)
    first._carry_email_selection()
    first._orchestrator.email_selection.update(address="work@example.com")

    other = _session(8, resumed=True)
    other._carry_email_selection()
    assert other._orchestrator.email_selection == {}


async def test_a_stale_choice_is_not_carried(monkeypatch) -> None:
    first = _session(7, resumed=False)
    first._carry_email_selection()
    first._orchestrator.email_selection.update(address="work@example.com")
    mine, _at = session_mod._EMAIL_SELECTIONS[7]
    session_mod._EMAIL_SELECTIONS[7] = (mine, _at - session_mod._RESUME_TTL_S - 1)

    again = _session(7, resumed=True)
    again._carry_email_selection()
    assert again._orchestrator.email_selection == {}


async def test_old_entries_are_dropped() -> None:
    first = _session(7, resumed=False)
    first._carry_email_selection()
    mine, at = session_mod._EMAIL_SELECTIONS[7]
    session_mod._EMAIL_SELECTIONS[7] = (mine, at - session_mod._RESUME_TTL_S - 1)
    _session(8, resumed=False)._carry_email_selection()
    assert 7 not in session_mod._EMAIL_SELECTIONS
