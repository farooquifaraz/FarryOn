"""The email tools' memory follows a RESUMED conversation across a reconnect.

Device-seen 2026-09-12: a stuck_reconnect dropped the socket mid-flow; the
new connection resumed the Gemini conversation (so the model remembered the
user had said "primary") but the orchestrator started with an empty
``email_selection``, and the tool's asked-this-session gate threw the answer
away and asked again.
"""

from __future__ import annotations

from app.ws import session as ws_session
from app.ws.session import email_memory_for


def setup_function() -> None:
    ws_session._EMAIL_MEMORY.clear()


def test_fresh_conversation_starts_empty_and_is_registered() -> None:
    sel, thr = email_memory_for(7, resumed=False, now=100.0)
    assert sel == {} and thr == {}
    sel["address"] = "me@x.com"
    assert ws_session._EMAIL_MEMORY[7][0] is sel


def test_resumed_conversation_inherits_the_same_dicts() -> None:
    sel, thr = email_memory_for(7, resumed=False, now=100.0)
    sel.update(asked=True, address="me@x.com")
    thr["me@x.com:5"] = {"message_id": "<m5>"}
    again_sel, again_thr = email_memory_for(7, resumed=True, now=100.0 + 60)
    assert again_sel is sel and again_thr is thr
    assert again_sel["address"] == "me@x.com"


def test_a_new_conversation_never_inherits_even_within_the_ttl() -> None:
    """The spec: the mailbox question comes first in every NEW conversation."""
    sel, _ = email_memory_for(7, resumed=False, now=100.0)
    sel.update(asked=True, address="me@x.com")
    fresh, _ = email_memory_for(7, resumed=False, now=100.0 + 60)
    assert fresh is not sel and fresh == {}


def test_memory_expires_with_the_resume_ttl() -> None:
    sel, _ = email_memory_for(7, resumed=False, now=100.0)
    sel.update(asked=True, address="me@x.com")
    late, _ = email_memory_for(7, resumed=True, now=100.0 + ws_session._RESUME_TTL_S + 1)
    assert late is not sel and late == {}


def test_memory_is_per_user_and_anonymous_sessions_are_not_stored() -> None:
    a, _ = email_memory_for(1, resumed=False, now=0.0)
    a["address"] = "a@x.com"
    b, _ = email_memory_for(2, resumed=True, now=1.0)
    assert b == {}
    anon, _ = email_memory_for(None, resumed=True, now=2.0)
    assert anon == {} and None not in ws_session._EMAIL_MEMORY


def test_the_session_hands_the_memory_to_the_orchestrator() -> None:
    import inspect

    src = inspect.getsource(ws_session)
    assert "email_memory_for(" in src
    assert "email_selection=email_selection" in src
    assert "email_threads=email_threads" in src
