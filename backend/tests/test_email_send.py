"""Tests for the send_email SMTP tool (network is patched out)."""

from __future__ import annotations

import smtplib

import pytest

from app.tools import email_send
from app.tools.base import ToolContext
from app.tools.email_send import SendEmailTool

pytestmark = pytest.mark.asyncio


async def test_send_email_without_config(db_session) -> None:
    ctx = ToolContext(session=db_session, email=None)
    result = await SendEmailTool().run(ctx, to="a@b.com", body="hi")
    assert result["ok"] is False
    assert "configured" in result["message"].lower()


async def test_send_email_requires_valid_recipient(db_session) -> None:
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await SendEmailTool().run(ctx, to="not-an-email", body="hi")
    assert result["ok"] is False
    assert "recipient" in result["message"].lower()


async def test_send_email_sends(db_session, monkeypatch) -> None:
    captured: dict = {}

    def fake_send(host, address, password, to, subject, body):
        captured.update(
            host=host, address=address, to=to, subject=subject, body=body
        )

    monkeypatch.setattr(email_send, "_send", fake_send)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await SendEmailTool().run(
        ctx, to="faraz@gmail.com", subject="Hi", body="See you tomorrow"
    )
    assert result["ok"] is True
    assert result["sent"] is True
    assert captured["host"] == "smtp.gmail.com"
    assert captured["to"] == "faraz@gmail.com"
    assert captured["body"] == "See you tomorrow"


async def test_send_email_auth_error_is_graceful(db_session, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise smtplib.SMTPAuthenticationError(535, b"bad creds")

    monkeypatch.setattr(email_send, "_send", boom)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "wrong"},
    )
    result = await SendEmailTool().run(ctx, to="a@b.com", body="hi")
    assert result["ok"] is False
    assert "sign in" in result["message"].lower()
