"""Tests for the read_emails IMAP tool (no real network — _fetch is patched)."""

from __future__ import annotations

import imaplib
from email.message import EmailMessage

from app.tools import email_read
from app.tools.base import ToolContext
from app.tools.email_read import ReadEmailsTool

# asyncio_mode=auto (pytest.ini) runs async tests automatically, so only the
# coroutine tests below need awaiting; the sync helper tests stay unmarked.


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


# --------------------------------------------------------------------------- #
# Helper-function unit tests (_decode / _snippet / _fetch_emails) — these were
# previously uncovered; they hold the fiddly RFC-2047 / MIME / IMAP parsing.
# --------------------------------------------------------------------------- #


def test_decode_handles_none_plain_and_rfc2047() -> None:
    """_decode tolerates None, passes plain text, and decodes encoded words."""
    assert email_read._decode(None) == ""
    assert email_read._decode("  Plain Subject  ") == "Plain Subject"
    # RFC 2047 encoded-word: "Café" in UTF-8 quoted-printable.
    assert email_read._decode("=?utf-8?q?Caf=C3=A9?=") == "Café"


def test_snippet_from_simple_and_multipart_messages() -> None:
    """_snippet extracts collapsed text from plain and multipart bodies."""
    simple = EmailMessage()
    simple.set_content("Hello   there\n\nworld")
    assert email_read._snippet(simple) == "Hello there world"

    multipart = EmailMessage()
    multipart.set_content("plain body text")
    multipart.add_alternative("<p>html body</p>", subtype="html")
    # The text/plain part is preferred over the HTML alternative.
    assert "plain body text" in email_read._snippet(multipart)


def test_snippet_is_truncated_to_max_chars() -> None:
    """A long body is clipped to _SNIPPET_CHARS."""
    msg = EmailMessage()
    msg.set_content("x " * 500)
    assert len(email_read._snippet(msg)) <= email_read._SNIPPET_CHARS


class _FakeIMAP:
    """Minimal stand-in for imaplib.IMAP4_SSL used by _fetch_emails."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.logged_out = False
        self._messages: dict[bytes, bytes] = {}
        for i in range(1, 3):
            m = EmailMessage()
            m["From"] = f"Sender {i} <s{i}@x.com>"
            m["Subject"] = f"Subject {i}"
            m["Date"] = "Mon, 01 Jun 2026 10:00:00 +0000"
            m.set_content(f"body {i}")
            self._messages[str(i).encode()] = m.as_bytes()

    def login(self, address: str, password: str) -> None:
        if password == "wrong":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list]:
        return ("OK", [b"2"])

    def search(self, charset, *criteria) -> tuple[str, list[bytes]]:
        return ("OK", [b" ".join(self._messages.keys())])

    def fetch(self, mid: bytes, spec: str) -> tuple[str, list]:
        return ("OK", [(b"1 (RFC822 {})", self._messages[mid])])

    def logout(self) -> None:
        self.logged_out = True


def test_fetch_emails_parses_and_orders_newest_first(monkeypatch) -> None:
    """_fetch_emails returns parsed dicts, newest-first, and logs out."""
    created: list[_FakeIMAP] = []

    def factory(host):
        imap = _FakeIMAP(host)
        created.append(imap)
        return imap

    monkeypatch.setattr(email_read.imaplib, "IMAP4_SSL", factory)

    out = email_read._fetch_emails(
        "imap.gmail.com", "me@x.com", "pw", limit=10, query=None
    )

    assert [e["subject"] for e in out] == ["Subject 2", "Subject 1"]  # newest 1st
    assert out[0]["from"] == "Sender 2 <s2@x.com>"
    assert out[0]["date"] is not None and out[0]["snippet"] == "body 2"
    assert created[0].logged_out is True  # connection always cleaned up


def test_fetch_emails_empty_search_returns_empty(monkeypatch) -> None:
    """A search that matches nothing yields an empty list, not an error."""

    class _Empty(_FakeIMAP):
        def search(self, charset, *criteria):
            return ("OK", [b""])

    monkeypatch.setattr(email_read.imaplib, "IMAP4_SSL", _Empty)
    assert email_read._fetch_emails("h", "a", "p", 10, None) == []
