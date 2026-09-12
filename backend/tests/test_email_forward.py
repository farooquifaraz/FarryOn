"""forward_email: the original (and its attachments) go out under Fwd:."""

from __future__ import annotations

import email as emaillib
from email.message import EmailMessage

import pytest

from app.tools import email_send
from app.tools.base import ToolContext
from app.tools.email_send import ForwardEmailTool, _build_forward



def _original(*, with_attachment: bool = True, subject: str = "Q3 report") -> bytes:
    msg = EmailMessage()
    msg["From"] = "Ali <ali@x.com>"
    msg["To"] = "me@gmail.com"
    msg["Cc"] = "boss@x.com"
    msg["Subject"] = subject
    msg["Date"] = "Fri, 11 Sep 2026 10:00:00 +0400"
    msg["Message-ID"] = "<orig@x.com>"
    msg.set_content("Please find the report attached.\n\nAli")
    if with_attachment:
        msg.add_attachment(
            b"%PDF-1.4 fake", maintype="application", subtype="pdf",
            filename="report.pdf",
        )
    return msg.as_bytes()


def test_build_forward_quotes_the_original_and_reattaches() -> None:
    msg, info = _build_forward(
        _original(), sender="me@gmail.com", to=["faraz@y.com"], cc=[], bcc=[],
        note="FYI — see below.", with_attachments=True,
    )
    assert msg["Subject"] == "Fwd: Q3 report"
    assert msg["To"] == "faraz@y.com"
    assert msg["From"] == "me@gmail.com"
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert body.startswith("FYI — see below.")
    assert "---------- Forwarded message ----------" in body
    assert "From: Ali <ali@x.com>" in body
    assert "Subject: Q3 report" in body
    assert "Cc: boss@x.com" in body
    assert "Please find the report attached." in body
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ["report.pdf"]
    assert info["attachments"] == ["report.pdf"]
    assert info["attachments_skipped"] == []
    assert info["original_from"] == "Ali <ali@x.com>"


def test_build_forward_keeps_an_existing_fwd_prefix_and_no_note() -> None:
    msg, info = _build_forward(
        _original(with_attachment=False, subject="FW: hello"),
        sender="me@gmail.com", to=["a@b.com"], cc=["c@d.com"], bcc=["e@f.com"],
        note="   ", with_attachments=True,
    )
    assert msg["Subject"] == "FW: hello"
    assert msg["Cc"] == "c@d.com" and msg["Bcc"] == "e@f.com"
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert body.startswith("---------- Forwarded message ----------")
    assert info["attachments"] == []


def test_build_forward_skips_attachments_over_budget(monkeypatch) -> None:
    monkeypatch.setattr(email_send, "_MAX_FORWARD_ATTACH_BYTES", 4)
    msg, info = _build_forward(
        _original(), sender="me@gmail.com", to=["a@b.com"], cc=[], bcc=[],
        note="", with_attachments=True,
    )
    assert list(msg.iter_attachments()) == []
    assert info["attachments_skipped"] == ["report.pdf"]


def _ctx(db_session, **over) -> ToolContext:
    return ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
        **over,
    )


@pytest.mark.asyncio
async def test_forward_needs_an_account_first(db_session, monkeypatch) -> None:
    delivered = {"n": 0}
    monkeypatch.setattr(email_send, "_deliver", lambda *a, **k: delivered.__setitem__("n", 1))
    ctx = ToolContext(session=db_session, email=None)
    result = await ForwardEmailTool().run(ctx, to="a@b.com", uid="1")
    assert result["ok"] is False and result["status"] == "no_account"
    assert delivered["n"] == 0


@pytest.mark.asyncio
async def test_forward_by_uid_sends_with_attachment(db_session, monkeypatch) -> None:
    delivered: dict = {}

    def fake_deliver(host, port, address, password, msg):
        delivered.update(host=host, port=port, address=address, msg=msg)

    fetched: dict = {}

    def fake_raw(host, address, password, *, uid=None, query=None, range_="week"):
        fetched.update(host=host, uid=uid, query=query)
        return {"uid": "31", "raw": _original(), "size": 2048, "truncated": False}

    monkeypatch.setattr(email_send, "_deliver", fake_deliver)
    monkeypatch.setattr(email_send.email_read, "fetch_raw_message", fake_raw)
    result = await ForwardEmailTool().run(
        _ctx(db_session), account="primary", to="faraz@y.com", uid="31",
        note="Have a look", cc="boss@x.com",
    )
    assert result["ok"] is True and result["sent"] is True
    assert result["subject"] == "Fwd: Q3 report"
    assert result["attachments"] == ["report.pdf"]
    assert result["cc"] == ["boss@x.com"]
    assert fetched == {"host": "imap.gmail.com", "uid": "31", "query": None}
    sent = delivered["msg"]
    assert sent["To"] == "faraz@y.com" and sent["Cc"] == "boss@x.com"
    assert [p.get_filename() for p in sent.iter_attachments()] == ["report.pdf"]
    assert delivered["host"] == "smtp.gmail.com" and delivered["port"] == 587


@pytest.mark.asyncio
async def test_forward_by_query_when_nothing_matches(db_session, monkeypatch) -> None:
    monkeypatch.setattr(email_send, "_deliver", lambda *a, **k: None)
    monkeypatch.setattr(
        email_send.email_read, "fetch_raw_message", lambda *a, **k: None,
    )
    result = await ForwardEmailTool().run(
        _ctx(db_session), account="primary", to="a@b.com", query="unicorn",
    )
    assert result["ok"] is False
    assert "no matching" in result["message"].lower()


@pytest.mark.asyncio
async def test_forward_without_uid_or_query_asks(db_session, monkeypatch) -> None:
    monkeypatch.setattr(email_send, "_deliver", lambda *a, **k: None)
    result = await ForwardEmailTool().run(_ctx(db_session), account="primary", to="a@b.com")
    assert result["ok"] is False
    assert "which email" in result["message"].lower()


@pytest.mark.asyncio
async def test_forward_bad_recipient_is_refused_before_any_imap(db_session, monkeypatch) -> None:
    touched = {"n": 0}

    def raw(*_a, **_k):
        touched["n"] += 1

    monkeypatch.setattr(email_send.email_read, "fetch_raw_message", raw)
    result = await ForwardEmailTool().run(
        _ctx(db_session), account="primary", to="nope", uid="1",
    )
    assert result["ok"] is False and touched["n"] == 0


@pytest.mark.asyncio
async def test_forward_of_a_huge_mail_drops_attachments_and_says_so(
    db_session, monkeypatch
) -> None:
    delivered: dict = {}
    monkeypatch.setattr(
        email_send, "_deliver", lambda h, p, a, pw, msg: delivered.update(msg=msg),
    )
    monkeypatch.setattr(
        email_send.email_read, "fetch_raw_message",
        lambda *a, **k: {"uid": "9", "raw": _original(), "size": 10**9, "truncated": True},
    )
    result = await ForwardEmailTool().run(
        _ctx(db_session), account="primary", to="a@b.com", uid="9",
    )
    assert result["ok"] is True
    assert list(delivered["msg"].iter_attachments()) == []
    assert "too large" in result["_instruction"].lower()


@pytest.mark.asyncio
async def test_forward_is_idempotent(db_session, monkeypatch) -> None:
    n = {"sent": 0}
    monkeypatch.setattr(email_send, "_deliver", lambda *a, **k: n.__setitem__("sent", n["sent"] + 1))
    monkeypatch.setattr(
        email_send.email_read, "fetch_raw_message",
        lambda *a, **k: {"uid": "555", "raw": _original(), "size": 1, "truncated": False},
    )
    ctx = _ctx(db_session)
    first = await ForwardEmailTool().run(ctx, account="primary", to="idem@b.com", uid="555")
    again = await ForwardEmailTool().run(ctx, account="primary", to="idem@b.com", uid="555")
    assert first["ok"] and again.get("deduped") is True
    assert n["sent"] == 1


@pytest.mark.asyncio
async def test_forward_smtp_auth_failure_is_graceful(db_session, monkeypatch) -> None:
    import smtplib

    def boom(*_a, **_k):
        raise smtplib.SMTPAuthenticationError(535, b"bad")

    monkeypatch.setattr(email_send, "_deliver", boom)
    monkeypatch.setattr(
        email_send.email_read, "fetch_raw_message",
        lambda *a, **k: {"uid": "1", "raw": _original(), "size": 1, "truncated": False},
    )
    result = await ForwardEmailTool().run(
        _ctx(db_session), account="primary", to="auth@b.com", uid="1",
    )
    assert result["ok"] is False and "sign in" in result["message"].lower()


def test_forwarded_message_parses_back_cleanly() -> None:
    """What we build is a valid RFC 5322 message a client can open."""
    msg, _ = _build_forward(
        _original(), sender="me@gmail.com", to=["a@b.com"], cc=[], bcc=[],
        note="note", with_attachments=True,
    )
    parsed = emaillib.message_from_bytes(msg.as_bytes())
    assert parsed.is_multipart()
    assert parsed["Subject"] == "Fwd: Q3 report"
    assert parsed["Message-ID"]
