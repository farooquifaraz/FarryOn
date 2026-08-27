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
    """With config, the tool returns the fetched messages + filters passed."""
    captured: dict = {}

    def fake_fetch(host, address, password, limit, query, category, range_,
                   full_body=False):
        captured.update(
            host=host, address=address, password=password, limit=limit,
            query=query, category=category, range_=range_,
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
    result = await ReadEmailsTool().run(
        ctx, limit=5, query="invoice", category="promotions", range="week"
    )

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["emails"][0]["subject"] == "Hi"
    assert captured["host"] == "imap.gmail.com"  # Gmail default
    assert captured["limit"] == 5
    assert captured["query"] == "invoice"
    assert captured["category"] == "promotions"
    assert captured["range_"] == "week"


async def test_gmail_query_builds_category_range_text() -> None:  # module asyncio mark
    """The Gmail search string combines category, range and free text."""
    q = email_read._gmail_query("promotions", "week", "from:amazon")
    assert "category:promotions" in q
    assert "newer_than:7d" in q
    assert "from:amazon" in q
    # No filters at all -> defaults to today.
    assert email_read._gmail_query(None, None, None) == "newer_than:1d"


async def test_read_emails_limit_is_clamped(db_session, monkeypatch) -> None:
    """An absurd limit is clamped to the max."""
    seen: dict = {}

    def fake_fetch(host, address, password, limit, query, category, range_,
                   full_body=False):
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


async def test_read_emails_retries_once_on_network_error(
    db_session, monkeypatch
) -> None:
    """One dropped connection is retried; the user never hears about it."""
    calls = {"n": 0}

    def flaky_fetch(host, address, password, limit, query, category, range_,
                    full_body=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("connection reset by peer")
        return [
            {"from": "A <a@x.com>", "subject": "Hi", "date": None,
             "snippet": "yo"},
        ]

    monkeypatch.setattr(email_read, "_fetch_emails", flaky_fetch)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await ReadEmailsTool().run(ctx)
    assert result["ok"] is True
    assert result["count"] == 1
    assert calls["n"] == 2


async def test_read_emails_auth_error_is_not_retried(
    db_session, monkeypatch
) -> None:
    """A wrong password fails ONCE — retrying it just doubles the delay."""
    calls = {"n": 0}

    def bad_auth(*_a, **_k):
        calls["n"] += 1
        raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    monkeypatch.setattr(email_read, "_fetch_emails", bad_auth)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "wrong"},
    )
    result = await ReadEmailsTool().run(ctx)
    assert result["ok"] is False
    assert calls["n"] == 1


async def test_read_emails_all_mailboxes_down_is_an_outage_not_empty(
    db_session, monkeypatch
) -> None:
    """Every account failing must NOT read as an empty inbox."""
    def down(*_a, **_k):
        raise TimeoutError("imap timed out")

    monkeypatch.setattr(email_read, "_fetch_emails", down)
    ctx = ToolContext(
        session=db_session,
        emails=[
            {"label": "Personal", "address": "me@gmail.com",
             "appPassword": "pw", "primary": True},
            {"label": "Work", "address": "w@work.com", "appPassword": "pw2"},
        ],
    )
    result = await ReadEmailsTool().run(ctx, account="all")
    assert result["ok"] is False
    assert "empty" in result["message"].lower()


async def test_read_emails_one_mailbox_down_is_flagged(
    db_session, monkeypatch
) -> None:
    """A partial outage returns the good mailbox AND names the bad one."""
    def half_down(host, address, password, limit, query, category, range_,
                  full_body=False):
        if "work" in address:
            raise TimeoutError("imap timed out")
        return [
            {"from": "A <a@x.com>", "subject": "Hi", "date": None,
             "snippet": "yo"},
        ]

    monkeypatch.setattr(email_read, "_fetch_emails", half_down)
    ctx = ToolContext(
        session=db_session,
        emails=[
            {"label": "Personal", "address": "me@gmail.com",
             "appPassword": "pw", "primary": True},
            {"label": "Work", "address": "w@work.com", "appPassword": "pw2"},
        ],
    )
    result = await ReadEmailsTool().run(ctx, account="all")
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["unreachable_accounts"] == ["Work"]
