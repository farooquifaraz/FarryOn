"""Outgoing mail goes draft → the user's answer → send, and the server
enforces it (``app.tools.email_drafts``).

Device 2026-09-12: the user said "ali at gmail", the model made up
``ali@gmail.com`` and sent in the same breath (E7.4); later a confirmed send
arrived without its body and failed (E7.1). Pinned here: nothing is sent
without a shown draft and a later user turn, the approved draft fills a
dropped body, a changed recipient or text is asked again, and the live
session always has the gate switched on.
"""

from __future__ import annotations

import time
from email.message import EmailMessage

import pytest

from app.agent.orchestrator import Orchestrator
from app.agent.tool_engine import ToolEngine
from app.db import base as db_base
from app.tools import email_drafts, email_send
from app.tools.base import Tool, ToolContext
from app.tools.email_send import ForwardEmailTool, SendEmailTool

pytestmark = pytest.mark.asyncio

_ACCOUNT = {"address": "me@gmail.com", "appPassword": "pw"}


class _Turns:
    def __init__(self) -> None:
        self.n = 1

    def __call__(self) -> int:
        return self.n


def _ctx(db, turns: _Turns) -> ToolContext:
    return ToolContext(
        session=db, email=_ACCOUNT, email_drafts={}, user_turn=turns,
    )


def _sends(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    def fake(host, port, address, password, to, subject, body,
             cc=None, bcc=None, headers=None):
        sent.append({"to": to, "subject": subject, "body": body, "cc": cc})

    monkeypatch.setattr(email_send, "_send", fake)
    return sent


async def test_a_first_call_only_returns_the_draft(db_session, monkeypatch) -> None:
    sent = _sends(monkeypatch)
    turns = _Turns()
    out = await SendEmailTool().run(
        _ctx(db_session, turns), to="ali@example.com", subject="Hi",
        body="Running late", account="primary",
    )
    assert sent == []
    assert out["status"] == "confirm_needed" and out["sent"] is False
    assert out["draft"]["to"] == ["ali@example.com"]
    assert out["draft"]["body"] == "Running late"
    assert "confirmed=true" in out["_instruction"]


async def test_confirmed_in_the_same_breath_sends_nothing(db_session, monkeypatch) -> None:
    # E7.4: the model showed nothing to the user and sent right away.
    sent = _sends(monkeypatch)
    turns = _Turns()
    ctx = _ctx(db_session, turns)
    await SendEmailTool().run(ctx, to="ali@example.com", body="x", account="primary")
    out = await SendEmailTool().run(
        ctx, to="ali@example.com", body="x", account="primary", confirmed=True,
    )
    assert sent == []
    assert out["status"] == "confirm_needed"
    assert "not answered" in out["_instruction"]


async def test_confirmed_without_a_draft_sends_nothing(db_session, monkeypatch) -> None:
    sent = _sends(monkeypatch)
    out = await SendEmailTool().run(
        _ctx(db_session, _Turns()), to="ali@example.com", body="x",
        account="primary", confirmed=True,
    )
    assert sent == [] and out["status"] == "confirm_needed"


async def test_the_yes_on_a_later_turn_sends_the_approved_draft(db_session, monkeypatch) -> None:
    sent = _sends(monkeypatch)
    turns = _Turns()
    ctx = _ctx(db_session, turns)
    await SendEmailTool().run(
        ctx, to="ali@example.com", subject="Late", body="Running 10 min late",
        cc="boss@example.com", account="primary",
    )
    turns.n += 1  # the user said "yes"
    # E7.1: the confirmed call dropped the body (and subject, cc).
    out = await SendEmailTool().run(
        ctx, to="ali@example.com", account="primary", confirmed=True,
    )
    assert out["sent"] is True
    assert sent == [{
        "to": "ali@example.com", "subject": "Late",
        "body": "Running 10 min late", "cc": ["boss@example.com"],
    }]
    # Sent once: another "yes" has no draft behind it.
    again = await SendEmailTool().run(
        ctx, to="ali@example.com", account="primary", confirmed=True,
    )
    assert again["status"] == "confirm_needed" and len(sent) == 1


async def test_a_changed_recipient_is_asked_again(db_session, monkeypatch) -> None:
    sent = _sends(monkeypatch)
    turns = _Turns()
    ctx = _ctx(db_session, turns)
    await SendEmailTool().run(ctx, to="ali@example.com", body="x", account="primary")
    turns.n += 1
    out = await SendEmailTool().run(
        ctx, to="ali.khan@example.com", body="x", account="primary", confirmed=True,
    )
    assert sent == []
    assert out["status"] == "confirm_needed" and "to" in out["_instruction"]
    assert out["draft"]["to"] == ["ali.khan@example.com"]
    turns.n += 1  # the user approves the corrected draft
    ok = await SendEmailTool().run(
        ctx, to="ali.khan@example.com", account="primary", confirmed=True,
    )
    assert ok["sent"] is True and sent[0]["to"] == "ali.khan@example.com"


async def test_a_changed_text_is_asked_again(db_session, monkeypatch) -> None:
    sent = _sends(monkeypatch)
    turns = _Turns()
    ctx = _ctx(db_session, turns)
    await SendEmailTool().run(ctx, to="ali@example.com", body="See you at 5", account="primary")
    turns.n += 1
    out = await SendEmailTool().run(
        ctx, to="ali@example.com", body="See you at 6", account="primary",
        confirmed=True,
    )
    assert sent == [] and out["draft"]["body"] == "See you at 6"
    # Whitespace or case alone is not a change.
    turns.n += 1
    ok = await SendEmailTool().run(
        ctx, to="ALI@example.com", body="See you  at 6", account="primary",
        confirmed="true",
    )
    assert ok["sent"] is True and sent[0]["body"] == "See you at 6"


async def test_an_expired_draft_cannot_be_sent(db_session, monkeypatch) -> None:
    sent = _sends(monkeypatch)
    turns = _Turns()
    ctx = _ctx(db_session, turns)
    await SendEmailTool().run(ctx, to="ali@example.com", body="x", account="primary")
    ctx.email_drafts["send"]["at"] = time.monotonic() - email_drafts.DRAFT_TTL_S - 1
    turns.n += 1
    out = await SendEmailTool().run(
        ctx, to="ali@example.com", account="primary", confirmed=True,
    )
    assert sent == [] and out["status"] == "confirm_needed"


async def test_a_forward_is_shown_before_it_goes(db_session, monkeypatch) -> None:
    original = EmailMessage()
    original["From"] = "Bank <bank@example.com>"
    original["Subject"] = "Statement"
    original.set_content("hello")
    monkeypatch.setattr(
        email_send.email_read, "fetch_raw_message",
        lambda *a, **k: {"uid": "77", "raw": original.as_bytes(), "size": 10,
                         "truncated": False},
    )
    delivered: list = []
    monkeypatch.setattr(email_send, "_deliver", lambda *a: delivered.append(a[-1]))
    turns = _Turns()
    ctx = _ctx(db_session, turns)
    out = await ForwardEmailTool().run(
        ctx, to="ali@example.com", uid="77", account="primary",
    )
    assert delivered == [] and out["status"] == "confirm_needed"
    assert "Statement" in out["draft"]["email"]
    turns.n += 1
    ok = await ForwardEmailTool().run(
        ctx, to="ali@example.com", account="primary", confirmed=True,
    )
    assert ok["sent"] is True and len(delivered) == 1
    assert delivered[0]["Subject"] == "Fwd: Statement"


class _Probe(Tool):
    name = "probe"
    description = "records its context"
    parameters = {"type": "object", "properties": {}, "required": []}
    seen: list[ToolContext] = []

    async def run(self, ctx: ToolContext, **kwargs):
        _Probe.seen.append(ctx)
        return {"ok": True}


async def test_the_live_session_always_has_the_gate_on() -> None:
    from app.ai.events import ToolCallEvent
    from app.ai.mock import MockGateway

    async def notify(_: dict) -> None:
        return None

    orch = Orchestrator(
        engine=ToolEngine.from_tools([_Probe()]),
        gateway=MockGateway(system_prompt="s", tools=[]),
        sessionmaker=db_base.get_sessionmaker(),
        notify_client=notify,
        session_id="drafts",
    )
    orch.note_user_turn()
    await orch.handle_tool_call(ToolCallEvent(id="1", name="probe", args={}))
    ctx = _Probe.seen[-1]
    assert ctx.email_drafts is not None, "without it the send tools skip the gate"
    first = ctx.user_turn()
    orch.note_user_turn()
    assert ctx.user_turn() == first + 1
