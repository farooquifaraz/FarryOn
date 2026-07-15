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
