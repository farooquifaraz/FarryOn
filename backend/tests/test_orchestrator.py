"""Tests for the orchestrator's device contact-resolution round-trip."""

from __future__ import annotations

import asyncio

import pytest

from app.agent.orchestrator import Orchestrator

pytestmark = pytest.mark.asyncio


def _orchestrator(notify):
    return Orchestrator(
        engine=None,  # type: ignore[arg-type]
        gateway=None,  # type: ignore[arg-type]
        sessionmaker=None,  # type: ignore[arg-type]
        notify_client=notify,
    )


async def test_resolve_roundtrip_returns_device_payload():
    sent: list[dict] = []

    async def notify(msg):
        sent.append(msg)

    orch = _orchestrator(notify)

    async def device_reply():
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.01)
        req = sent[-1]
        orch.resolve_pending(
            req["requestId"],
            {"status": "found", "candidates": [{"contactId": "c1"}]},
        )

    task = asyncio.create_task(device_reply())
    result = await orch.request_contact_resolution("Kamlesh", "whatsapp")
    await task

    assert result["status"] == "found"
    assert sent[-1]["type"] == "resolve_contact_request"
    assert sent[-1]["name"] == "Kamlesh"
    assert sent[-1]["channel"] == "whatsapp"
    assert "requestId" in sent[-1]


async def test_resolve_caches_every_candidate_for_later_sends():
    """Every ambiguous candidate is cached by id AND by display name, so
    whichever one the user picks can be sent — on either channel — without
    re-resolving (a contact_id identifies a person, not a channel)."""
    sent: list[dict] = []

    async def notify(msg):
        sent.append(msg)

    orch = _orchestrator(notify)

    async def device_reply():
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.01)
        orch.resolve_pending(sent[-1]["requestId"], {
            "status": "ambiguous",
            "candidates": [
                {"contactId": "c4", "displayName": "Ahsan Bhai",
                 "phone": "+9715085"},
                {"contactId": "c5", "displayName": "Ahsan Chaccha",
                 "phone": "+9715084"},
            ],
        })

    task = asyncio.create_task(device_reply())
    await orch.request_contact_resolution("Ahsan", "telegram")
    await task

    # By id (what send_telegram dials).
    assert orch.recall_phone_by_id("c4") == "+9715085"
    assert orch.recall_phone_by_id("nope") is None
    # By the name the user picks (works across channels, case-insensitive).
    assert orch.recall_resolved("Ahsan Bhai") == "c4"
    assert orch.recall_resolved("ahsan chaccha") == "c5"
    assert orch.recall_phone("Ahsan Bhai") == "+9715085"
    # The ambiguous QUERY itself is NOT cached -> it stays ambiguous next time.
    assert orch.recall_resolved("Ahsan") is None


async def test_resolve_pending_unknown_id_is_harmless():
    async def notify(msg):
        pass

    orch = _orchestrator(notify)
    # Must not raise even when no Future is waiting on that id.
    orch.resolve_pending("nope", {"status": "found"})


async def test_resolve_timeout_degrades(monkeypatch):
    """If the device never replies, the tool gets index_unavailable, no hang."""
    import app.agent.orchestrator as orch_mod

    async def fast_timeout(awaitable, timeout):  # noqa: ARG001
        raise asyncio.TimeoutError

    monkeypatch.setattr(orch_mod.asyncio, "wait_for", fast_timeout)

    async def notify(msg):
        pass

    orch = _orchestrator(notify)
    result = await orch.request_contact_resolution("X", "whatsapp")
    assert result["status"] == "index_unavailable"


async def test_end_session_right_after_a_resume_is_refused():
    """A resumed session must not end itself on the previous conversation's tail.

    Device 2026-09-13 15:02: two fresh sessions fired end_session ten seconds
    in, before any user turn; the user could not say he had asked. Until a
    turn is heard, an end_session inside the resume guard window is refused
    and the model is told why — nothing is dispatched.
    """
    import time

    from app.ai.events import ToolCallEvent

    told: list[tuple] = []

    class _Gateway:
        async def send_tool_result(self, call_id, name, result, ok=True):
            told.append((call_id, name, ok))

    orch = _orchestrator(lambda msg: asyncio.sleep(0))
    orch._gateway = _Gateway()  # type: ignore[assignment]
    orch.resume_guard_until = time.monotonic() + 30
    orch.user_turns_heard = 0

    result = await orch.handle_tool_call(
        ToolCallEvent(id="c1", name="end_session", args={})
    )
    assert result.ok is False
    assert "resumed" in (result.error or "")
    assert told == [("c1", "end_session", False)]

    # Once the user has been heard, the guard no longer applies (the call
    # then goes to the real engine, which this test does not wire up).
    orch.user_turns_heard = 1
    assert not (
        orch.user_turns_heard == 0 and time.monotonic() < orch.resume_guard_until
    )


async def test_the_same_call_again_without_a_reply_is_refused_but_a_new_ask_is_not():
    """Live 2026-09-13 00:49: read_emails 5x in 45 s, silence after three.

    The second identical call with no spoken reply in between is refused with
    a reason; different arguments pass; a spoken reply or a new user turn
    clears the slate, so "Farry, again?" is never blocked.
    """
    from app.ai.events import ToolCallEvent

    told: list[tuple] = []

    class _Gateway:
        async def send_tool_result(self, call_id, name, result, ok=True):
            told.append((call_id, name, ok, result))

    orch = _orchestrator(lambda msg: asyncio.sleep(0))
    orch._gateway = _Gateway()  # type: ignore[assignment]
    orch.note_user_turn()

    a = {"account": "primary", "limit": 5}
    assert orch._repeat_refusal(ToolCallEvent(id="1", name="read_emails", args=a)) is None
    r = await orch.handle_tool_call(ToolCallEvent(id="2", name="read_emails", args=dict(a)))
    assert r.ok is False and "already called" in (r.error or "")
    assert told[-1][:3] == ("2", "read_emails", False)
    # Different arguments are a different question.
    assert orch._repeat_refusal(
        ToolCallEvent(id="3", name="read_emails", args={"account": "secondary"})
    ) is None
    # The model spoke: a fresh slate.
    orch.note_assistant_spoke()
    assert orch._repeat_refusal(ToolCallEvent(id="4", name="read_emails", args=a)) is None
    # The user asked again (any wording): a fresh slate too.
    orch.note_user_turn()
    assert orch._repeat_refusal(ToolCallEvent(id="5", name="read_emails", args=a)) is None
    # And a runaway with ever-changing arguments still stops at the cap.
    orch.note_user_turn()
    for i in range(5):
        assert orch._repeat_refusal(
            ToolCallEvent(id=f"w{i}", name="web_search", args={"q": f"thing {i}"})
        ) is None
    assert "5 times" in (
        orch._repeat_refusal(ToolCallEvent(id="w9", name="web_search", args={"q": "x"})) or ""
    )
