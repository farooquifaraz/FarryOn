"""Tests for the send_email SMTP tool (network is patched out)."""

from __future__ import annotations

import smtplib

import pytest

from app.tools import email_send
from app.tools.base import ToolContext
from app.tools.email_send import CONFIRM_SEND, SendEmailTool, draft_token

pytestmark = pytest.mark.asyncio


async def send_confirmed(ctx, **kw):
    """The real two-call flow: the draft call, then the same call with its
    token. A refusal before the gate (no account, bad address) is returned
    as-is, exactly as the model would see it."""
    first = await SendEmailTool().run(ctx, **kw)
    if first.get("status") != CONFIRM_SEND:
        return first
    assert first["ok"] is False and first["sent"] is False
    return await SendEmailTool().run(ctx, confirm=first["confirm_token"], **kw)


async def test_send_email_without_config(db_session) -> None:
    ctx = ToolContext(session=db_session, email=None)
    result = await send_confirmed(ctx, account="primary", to="a@b.com", body="hi")
    assert result["ok"] is False
    assert result["status"] == "no_account"
    assert "no email account is registered" in result["message"].lower()


async def test_send_email_requires_valid_recipient(db_session) -> None:
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await send_confirmed(ctx, account="primary", to="not-an-email", body="hi")
    assert result["ok"] is False
    # The hardened validator rejects an incomplete address by name.
    msg = result["message"].lower()
    assert "email" in msg or "address" in msg


def _capture(store: dict):
    """A ``_send`` double that records every argument by name."""

    def fake_send(host, port, address, password, to, subject, body,
                  cc=None, bcc=None, headers=None):
        store.update(
            host=host, port=port, address=address, to=to, subject=subject,
            body=body, cc=cc, bcc=bcc, headers=headers or {},
        )

    return fake_send


async def test_send_email_sends(db_session, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await send_confirmed(
        ctx, to="faraz@gmail.com", subject="Hi", body="See you tomorrow",
        account="primary",
    )
    assert result["ok"] is True
    assert result["sent"] is True
    assert captured["host"] == "smtp.gmail.com"
    assert captured["port"] == 587
    assert captured["to"] == "faraz@gmail.com"
    assert captured["body"] == "See you tomorrow"
    # A plain new mail carries no threading headers and no copies.
    assert captured["headers"] == {}
    assert captured["cc"] is None and captured["bcc"] is None
    assert "threaded" not in result


async def test_send_email_custom_host_and_port(db_session, monkeypatch) -> None:
    """Custom SMTP host + 465 port (e.g. Hostinger) are passed through."""
    seen: dict = {}

    def fake_send(host, port, address, password, to, subject, body, *a, **k):
        seen.update(host=host, port=port)

    monkeypatch.setattr(email_send, "_send", fake_send)
    ctx = ToolContext(
        session=db_session,
        email={
            "address": "me@omaemirates.com", "appPassword": "pw",
            "smtpHost": "smtp.hostinger.com", "smtpPort": 465,
        },
    )
    result = await send_confirmed(ctx, account="primary", to="a@b.com", body="hi")
    assert result["ok"] is True
    assert seen["host"] == "smtp.hostinger.com"
    assert seen["port"] == 465


async def test_send_email_auth_error_is_graceful(db_session, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise smtplib.SMTPAuthenticationError(535, b"bad creds")

    monkeypatch.setattr(email_send, "_send", boom)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "wrong"},
    )
    result = await send_confirmed(ctx, account="primary", to="a@b.com", body="hi")
    assert result["ok"] is False
    assert "sign in" in result["message"].lower()


# ---- CC / BCC ------------------------------------------------------------------

async def test_send_email_cc_and_bcc_are_parsed_and_sent(db_session, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await send_confirmed(
        ctx, account="primary", to="a@b.com", body="hi",
        cc="boss@work.com, Ali <ali@x.com>; boss@work.com", bcc="me2@y.org",
    )
    assert result["ok"] is True
    assert captured["cc"] == ["boss@work.com", "ali@x.com"]  # deduped, name stripped
    assert captured["bcc"] == ["me2@y.org"]
    assert result["cc"] == ["boss@work.com", "ali@x.com"]
    assert result["bcc"] == ["me2@y.org"]


async def test_send_email_bad_cc_address_blocks_the_send(db_session, monkeypatch) -> None:
    sent = {"n": 0}

    def fake_send(*_a, **_k):
        sent["n"] += 1

    monkeypatch.setattr(email_send, "_send", fake_send)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await send_confirmed(
        ctx, account="primary", to="a@b.com", body="hi", cc="not an address",
    )
    assert result["ok"] is False
    assert "cc" in result["message"].lower()
    assert sent["n"] == 0


async def test_send_email_several_recipients(db_session, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await send_confirmed(
        ctx, account="primary", to="a@b.com, c@d.com", body="hi",
    )
    assert result["ok"] is True
    assert captured["to"] == "a@b.com, c@d.com"


# ---- reply threading -------------------------------------------------------------

THREAD = {
    "uid": "77", "message_id": "<orig-1@x.com>",
    "references": "<root-0@x.com>", "subject": "Invoice 42",
    "from_email": "ali@x.com", "reply_to_email": "ali@x.com",
}


async def test_reply_by_uid_threads_from_the_session_cache(db_session, monkeypatch) -> None:
    """A uid the read tools cached → In-Reply-To / References / Re: subject,
    with no IMAP round trip."""
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))
    touched = {"n": 0}

    def no_imap(*_a, **_k):
        touched["n"] += 1
        raise AssertionError("the cache should have answered")

    monkeypatch.setattr(email_send.email_read, "fetch_thread_headers", no_imap)
    cache = {"me@gmail.com:77": THREAD, "<orig-1@x.com>": THREAD}
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
        email_threads=cache,
    )
    result = await send_confirmed(
        ctx, account="primary", to="ali@x.com", body="Paid today.", reply_to_uid="77",
    )
    assert result["ok"] is True
    assert result["threaded"] is True
    assert result["subject"] == "Re: Invoice 42"
    assert captured["headers"] == {
        "In-Reply-To": "<orig-1@x.com>",
        "References": "<root-0@x.com> <orig-1@x.com>",
    }
    assert touched["n"] == 0


async def test_reply_by_uid_falls_back_to_one_header_fetch(db_session, monkeypatch) -> None:
    """A uid the cache never saw (fresh session) → one IMAP header fetch."""
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))
    asked: dict = {}

    def fake_headers(host, address, password, uid):
        asked.update(host=host, address=address, uid=uid)
        return {**THREAD, "references": ""}

    monkeypatch.setattr(email_send.email_read, "fetch_thread_headers", fake_headers)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
        email_threads={},
    )
    result = await send_confirmed(
        ctx, account="primary", to="ali@x.com", body="ok", reply_to_uid="77",
        subject="Invoice 42",
    )
    assert asked == {"host": "imap.gmail.com", "address": "me@gmail.com", "uid": "77"}
    assert result["threaded"] is True
    assert result["subject"] == "Re: Invoice 42"  # same subject → Re: added
    assert captured["headers"]["References"] == "<orig-1@x.com>"


async def test_reply_keeps_an_existing_re_subject(db_session, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
        email_threads={"me@gmail.com:77": THREAD},
    )
    result = await send_confirmed(
        ctx, account="primary", to="ali@x.com", body="ok", reply_to_uid="77",
        subject="RE: Invoice 42",
    )
    assert result["subject"] == "RE: Invoice 42"


async def test_reply_with_message_id_only_is_honoured(db_session, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
        email_threads={},
    )
    result = await send_confirmed(
        ctx, account="primary", to="ali@x.com", body="ok", subject="Re: x",
        in_reply_to="<abc@x.com>",
    )
    assert result["threaded"] is True
    assert captured["headers"] == {"In-Reply-To": "<abc@x.com>", "References": "<abc@x.com>"}


async def test_reply_whose_lookup_fails_still_sends_unthreaded(db_session, monkeypatch) -> None:
    """Threading is best-effort: a failed header fetch never blocks the send."""
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))

    def boom(*_a, **_k):
        raise OSError("imap down")

    monkeypatch.setattr(email_send.email_read, "fetch_thread_headers", boom)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
        email_threads={},
    )
    result = await send_confirmed(
        ctx, account="primary", to="ali@x.com", body="ok", subject="Hello",
        reply_to_uid="5",
    )
    assert result["ok"] is True
    assert result["threaded"] is False
    assert captured["headers"] == {}


async def test_dedupe_fingerprint_includes_copies(db_session, monkeypatch) -> None:
    """The same mail to the same person but with a new cc is a different send."""
    sent = {"n": 0}

    def fake_send(*_a, **_k):
        sent["n"] += 1

    monkeypatch.setattr(email_send, "_send", fake_send)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    body = "dedupe-body-" + str(id(ctx))
    first = await send_confirmed(ctx, account="primary", to="a@b.com", body=body)
    again = await send_confirmed(ctx, account="primary", to="a@b.com", body=body)
    with_cc = await send_confirmed(
        ctx, account="primary", to="a@b.com", body=body, cc="c@d.com",
    )
    assert first["ok"] and again.get("deduped") is True
    assert with_cc["ok"] is True and "deduped" not in with_cc
    assert sent["n"] == 2


# ---- the confirmation gate (device-seen 2026-09-12: sent with no draft, no yes) ----

async def test_first_call_is_a_draft_and_sends_nothing(db_session, monkeypatch) -> None:
    sent = {"n": 0}
    monkeypatch.setattr(email_send, "_send", lambda *a, **k: sent.__setitem__("n", sent["n"] + 1))
    ctx = ToolContext(
        session=db_session, email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    first = await SendEmailTool().run(
        ctx, account="primary", to="ali@gmail.com", body="Hi", cc="boss@x.com",
    )
    assert first["ok"] is False and first["sent"] is False
    assert first["status"] == CONFIRM_SEND
    assert first["draft"] == {
        "from": "me@gmail.com", "account": "me@gmail.com", "to": "ali@gmail.com",
        "subject": "(no subject)", "body": "Hi", "cc": ["boss@x.com"],
    }
    assert first["confirm_token"].startswith("ok-")
    assert "spell it out" in first["instructions"]
    assert "yes" in first["instructions"].lower()
    assert sent["n"] == 0


async def test_wrong_or_stale_token_sends_nothing(db_session, monkeypatch) -> None:
    sent = {"n": 0}
    monkeypatch.setattr(email_send, "_send", lambda *a, **k: sent.__setitem__("n", sent["n"] + 1))
    ctx = ToolContext(
        session=db_session, email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    guess = await SendEmailTool().run(
        ctx, account="primary", to="a@b.com", body="hi", confirm="ok-deadbeef00",
    )
    assert guess["status"] == CONFIRM_SEND and sent["n"] == 0
    # A token for one draft does not send a different one.
    first = await SendEmailTool().run(ctx, account="primary", to="a@b.com", body="hi")
    changed = await SendEmailTool().run(
        ctx, account="primary", to="a@b.com", body="hi, changed", confirm=first["confirm_token"],
    )
    assert changed["status"] == CONFIRM_SEND and sent["n"] == 0
    assert changed["confirm_token"] != first["confirm_token"]
    other_to = await SendEmailTool().run(
        ctx, account="primary", to="c@d.com", body="hi", confirm=first["confirm_token"],
    )
    assert other_to["status"] == CONFIRM_SEND and sent["n"] == 0
    # The matching token does.
    done = await SendEmailTool().run(
        ctx, account="primary", to="a@b.com", body="hi", confirm=first["confirm_token"],
    )
    assert done["ok"] is True and done["sent"] is True and sent["n"] == 1


async def test_missing_body_is_asked_for_not_a_validation_error(db_session, monkeypatch) -> None:
    """Device-seen 2026-09-12: the confirmed call came without `body` and the
    engine rejected it with an error the model could not act on."""
    sent = {"n": 0}
    monkeypatch.setattr(email_send, "_send", lambda *a, **k: sent.__setitem__("n", sent["n"] + 1))
    ctx = ToolContext(
        session=db_session, email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    first = await SendEmailTool().run(ctx, account="primary", to="a@b.com", body="See you at 10")
    no_body = await SendEmailTool().run(
        ctx, account="primary", to="a@b.com", confirm=first["confirm_token"],
    )
    assert no_body["ok"] is False and no_body["status"] == "needs_body"
    assert "body" in no_body["instructions"]
    assert sent["n"] == 0
    # The draft text plus the SAME token sends — no second read-back needed.
    done = await SendEmailTool().run(
        ctx, account="primary", to="a@b.com", body="See you at 10",
        confirm=first["confirm_token"],
    )
    assert done["ok"] is True and sent["n"] == 1
    # The engine schema no longer requires body, so the call reaches the tool.
    assert "body" not in SendEmailTool().parameters["required"]


async def test_draft_token_changes_with_every_field() -> None:
    base = ("me@x.com", "a@b.com", "", "", "Hi", "text", "")
    t = draft_token("send", *base)
    for i in range(len(base)):
        other = list(base)
        other[i] = other[i] + "x"
        assert draft_token("send", *other) != t, i
    assert draft_token("forward", *base) != t
    assert draft_token("send", *base) == t  # stable within the process


async def test_header_fetch_for_a_reply_is_cached_for_the_confirmed_call(
    db_session, monkeypatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(email_send, "_send", _capture(captured))
    calls = {"n": 0}

    def fake_headers(host, address, password, uid):
        calls["n"] += 1
        return {**THREAD, "references": ""}

    monkeypatch.setattr(email_send.email_read, "fetch_thread_headers", fake_headers)
    cache: dict = {}
    ctx = ToolContext(
        session=db_session, email={"address": "me@gmail.com", "appPassword": "pw"},
        email_threads=cache,
    )
    result = await send_confirmed(
        ctx, account="primary", to="ali@x.com", body="ok", reply_to_uid="77",
    )
    assert result["threaded"] is True
    assert calls["n"] == 1  # draft call fetched; the confirmed call used the cache
    assert cache["me@gmail.com:77"]["message_id"] == "<orig-1@x.com>"
    assert cache["<orig-1@x.com>"]["uid"] == "77"
