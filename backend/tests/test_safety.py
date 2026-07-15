"""Tests for the messaging safety gates: sensitive-content + rate limiting."""

from __future__ import annotations

import pytest

from app.tools import ratelimit
from app.tools.base import ToolContext
from app.tools.safety import rate_gate, sensitive_gate
from app.tools.validators import scan_sensitive
from app.tools.whatsapp import SendWhatsAppTool

# No module-level ``pytestmark = pytest.mark.asyncio``: it would also mark the
# sync tests below (scan/gate/rate) as async and warn on each. Only the
# genuinely async tests carry the decorator.


def test_scan_sensitive_flags():
    assert scan_sensitive("your OTP is 123456") == ["an OTP / login code"]
    assert "a password / PIN" in scan_sensitive("my password is hunter2")
    assert "a CVV" in scan_sensitive("the cvv is 123")
    # a real (Luhn-valid) test card number
    assert "a card number" in scan_sensitive("card 4111 1111 1111 1111")
    # ordinary chat is clean
    assert scan_sensitive("see you at 5pm, bring the docs") == []
    assert scan_sensitive("") == []


def test_sensitive_gate_blocks_then_passes():
    blocked = sensitive_gate("my password is abc", confirm_sensitive=False)
    assert blocked and blocked["status"] == "sensitive_confirm_needed"
    # once the user has confirmed, it passes through
    assert sensitive_gate("my password is abc", confirm_sensitive=True) is None
    # clean message never blocks
    assert sensitive_gate("hi there", confirm_sensitive=False) is None


def test_rate_gate_trips_after_the_limit():
    ratelimit._hits.clear()
    sid = "sess-rate"
    allowed = sum(1 for _ in range(20) if rate_gate(sid) is None)
    assert allowed == ratelimit._MAX_PER_WINDOW
    assert rate_gate(sid)["status"] == "rate_limited"


@pytest.mark.asyncio
async def test_whatsapp_blocks_sensitive_then_sends(db_session):
    ratelimit._hits.clear()
    ctx = ToolContext(session=db_session, session_id="s1")
    r1 = await SendWhatsAppTool().run(
        ctx, message="your OTP is 998877", phone_number="+971501234567",
    )
    assert r1["ok"] is False and r1["status"] == "sensitive_confirm_needed"
    r2 = await SendWhatsAppTool().run(
        ctx, message="your OTP is 998877", phone_number="+971501234567",
        confirm_sensitive=True,
    )
    assert r2["ok"] is True and r2["action"] == "open_url"


@pytest.mark.asyncio
async def test_sent_messages_history(db_session):
    """A logged send shows up in list_sent_messages with channel + status."""
    from app.db import repo
    from app.tools.recall import ListSentMessagesTool

    await repo.add_outbound_message(
        db_session, contact="Faraz", text="hi there",
        status="telegram:delivered",
    )
    await db_session.commit()
    res = await ListSentMessagesTool().run(ToolContext(session=db_session))
    assert res["count"] >= 1
    top = res["messages"][0]
    assert top["to"] == "Faraz"
    assert top["channel"] == "telegram"
    assert top["status"] == "delivered"
