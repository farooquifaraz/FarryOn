"""Tests for the read_emails IMAP tool (no real network — _fetch is patched)."""

from __future__ import annotations

import imaplib

import pytest

from app.tools import email_read
from app.tools.base import ToolContext
from app.tools.email_read import ReadEmailsTool

pytestmark = pytest.mark.asyncio


async def test_read_emails_without_config(db_session) -> None:
    """No credentials -> a friendly 'configure it' result, not an error crash."""
    ctx = ToolContext(session=db_session, email=None)
    result = await ReadEmailsTool().run(ctx)
    assert result["ok"] is False
    assert "configured" in result["message"].lower()


async def test_read_emails_returns_messages(db_session, monkeypatch) -> None:
    """With config, the tool returns the fetched messages newest-first."""
    captured: dict = {}

    def fake_fetch(host, address, password, limit, query):
        captured.update(
            host=host, address=address, password=password,
            limit=limit, query=query,
        )
        return [
            {"from": "A <a@x.com>", "subject": "Hi", "date": None, "snippet": "yo"},
            {"from": "B <b@x.com>", "subject": "Re: Hi", "date": None, "snippet": ""},
        ]

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "app-pw"},
    )
    result = await ReadEmailsTool().run(ctx, limit=5, query="invoice")

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["emails"][0]["subject"] == "Hi"
    assert captured["host"] == "imap.gmail.com"  # Gmail default
    assert captured["address"] == "me@gmail.com"
    assert captured["limit"] == 5
    assert captured["query"] == "invoice"


async def test_read_emails_limit_is_clamped(db_session, monkeypatch) -> None:
    """An absurd limit is clamped to the max."""
    seen: dict = {}

    def fake_fetch(host, address, password, limit, query):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    await ReadEmailsTool().run(ctx, limit=9999)
    assert seen["limit"] == email_read._MAX_LIMIT


async def test_read_emails_auth_error_is_graceful(db_session, monkeypatch) -> None:
    """Bad credentials surface a friendly message, never raise."""
    def boom(*_a, **_k):
        raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    monkeypatch.setattr(email_read, "_fetch_emails", boom)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "wrong"},
    )
    result = await ReadEmailsTool().run(ctx)
    assert result["ok"] is False
    assert "password" in result["message"].lower()
