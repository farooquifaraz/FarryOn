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
    result = await ReadEmailsTool().run(ctx, account="primary")
    assert result["ok"] is False
    assert result["status"] == "no_account"
    assert "no email account is registered" in result["message"].lower()


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
        ctx, limit=5, query="invoice", category="promotions", range="week",
        account="primary",
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
    await ReadEmailsTool().run(ctx, limit=9999, account="primary")
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
    result = await ReadEmailsTool().run(ctx, account="primary")
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
    result = await ReadEmailsTool().run(ctx, account="primary")
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
    result = await ReadEmailsTool().run(ctx, account="primary")
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


# ---- honest counts ---------------------------------------------------------------

def _page(items, total):
    page = email_read.MailPage(items)
    page.total = total
    page.inbox_total = 1234
    page.inbox_unread = 56
    return page


async def test_read_emails_reports_total_and_has_more(db_session, monkeypatch) -> None:
    """Ten listed out of thirty → the model is told to say 'more than 10'."""
    def fake_fetch(host, address, password, limit, query, category, range_,
                   full_body=False):
        return _page(
            [{"uid": str(i), "from": "A <a@x.com>", "subject": f"m{i}",
              "date": None, "snippet": "", "_thread": {"uid": str(i)}}
             for i in range(limit)],
            total=30,
        )

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    ctx = ToolContext(
        session=db_session,
        email={"address": "me@gmail.com", "appPassword": "pw"},
        email_threads={},
    )
    result = await ReadEmailsTool().run(ctx, account="primary")
    assert result["count"] == 10 and result["total"] == 30
    assert result["has_more"] is True
    assert result["inbox_total"] == 1234 and result["inbox_unread"] == 56
    assert "more than 10" in result["_instruction"]
    # Private working keys never reach the model; the thread cache got them.
    assert all(not k.startswith("_") for e in result["emails"] for k in e)
    assert "me@gmail.com:3" in ctx.email_threads


async def test_read_emails_no_instruction_when_everything_fits(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        email_read, "_fetch_emails",
        lambda *a, **k: _page([{"from": "A <a@x.com>", "subject": "x", "date": None,
                                "snippet": ""}], total=1),
    )
    ctx = ToolContext(
        session=db_session, email={"address": "me@gmail.com", "appPassword": "pw"},
    )
    result = await ReadEmailsTool().run(ctx, account="primary")
    assert result["has_more"] is False and "_instruction" not in result


async def test_read_emails_all_accounts_sums_totals(db_session, monkeypatch) -> None:
    def fake_fetch(host, address, password, limit, query, category, range_,
                   full_body=False):
        n = 15 if "work" in address else 4
        return _page(
            [{"from": "A <a@x.com>", "subject": address, "date": None, "snippet": ""}],
            total=n,
        )

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    ctx = ToolContext(
        session=db_session,
        emails=[
            {"label": "Personal", "address": "me@gmail.com", "appPassword": "pw", "primary": True},
            {"label": "Work", "address": "w@work.com", "appPassword": "pw2"},
        ],
    )
    result = await ReadEmailsTool().run(ctx, account="all")
    assert result["total"] == 19 and result["count"] == 2
    assert result["has_more"] is True


# ---- the IMAP layer itself, against a scripted server ----------------------------

from email.message import EmailMessage  # noqa: E402


def _raw(uid: int, *, subject: str, frm: str, unread: bool = True,
         list_unsub: bool = False, html_only: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = "me@gmail.com"
    msg["Subject"] = subject
    msg["Date"] = f"Fri, 11 Sep 2026 1{uid % 10}:00:00 +0400"
    msg["Message-ID"] = f"<m{uid}@x.com>"
    msg["References"] = "<root@x.com>"
    if list_unsub:
        msg["List-Unsubscribe"] = "<mailto:u@x.com>"
    if html_only:
        msg.set_content("<p>Hello <b>there</b></p>", subtype="html")
    else:
        msg.set_content(f"Body of {subject}")
    return msg.as_bytes()


class _ScriptedIMAP:
    """Just enough of imaplib.IMAP4_SSL to drive _fetch_emails / set_seen."""

    messages = {
        101: dict(subject="Team lunch", frm="Sara <sara@x.com>", unread=False),
        102: dict(subject="URGENT: payment overdue", frm="Bank <alerts@bank.com>"),
        103: dict(subject="50% off everything", frm="Shop <noreply@shop.com>",
                  list_unsub=True, html_only=True),
    }
    flags = {101: b"\\Seen", 102: b"", 103: b"\\Seen"}
    labels = {101: b'"\\\\Inbox"', 102: b'"\\\\Important" "\\\\Inbox"', 103: b'"\\\\Inbox"'}
    log: list = []

    def __init__(self, host, timeout=None):
        self.host = host
        self.readonly = True
        type(self).log.append(("connect", host, timeout))

    def login(self, a, p):
        type(self).log.append(("login", a, p))
        return "OK", [b"done"]

    def select(self, box, readonly=True):
        self.readonly = readonly
        type(self).log.append(("select", box, readonly))
        return "OK", [b"3"]

    def status(self, box, what):
        return "OK", [b'"INBOX" (UNSEEN 1)']

    def uid(self, cmd, *args):
        type(self).log.append(("uid", cmd) + args)
        if cmd == "SEARCH":
            if "X-GM-RAW" in args and "is:unread" in args[-1]:
                return "OK", [b"102"]
            return "OK", [b"101 102 103"]
        if cmd == "STORE":
            return "OK", [b""]
        if cmd == "FETCH":
            uids = [int(u) for u in args[0].split(",")]
            spec = args[1]
            out = []
            for u in uids:
                raw = _raw(u, **self.messages[u])
                if "HEADER.FIELDS" in spec:
                    raw = raw.split(b"\n\n", 1)[0] + b"\n\n"
                if "RFC822.SIZE" in spec:
                    out.append(f"{u} (UID {u} RFC822.SIZE {len(raw)})".encode())
                    continue
                meta = (
                    f"{u} (UID {u} FLAGS ({self.flags[u].decode()}) X-GM-LABELS "
                    f"({self.labels[u].decode()}) BODY[] {{{len(raw)}}}"
                ).encode()
                out.append((meta, raw))
                out.append(b")")
            return "OK", out
        raise AssertionError(cmd)

    def logout(self):
        type(self).log.append(("logout",))


async def test_fetch_emails_parses_uids_flags_labels_and_scores(monkeypatch) -> None:
    _ScriptedIMAP.log.clear()
    monkeypatch.setattr(email_read.imaplib, "IMAP4_SSL", _ScriptedIMAP)
    page = email_read._fetch_emails(
        "imap.gmail.com", "me@gmail.com", "pw", 10, None, None, None,
        with_unread=True,
    )
    assert page.total == 3 and page.inbox_total == 3
    assert page.inbox_unread == 1 and page.unread_total == 1
    assert [e["uid"] for e in page] == ["103", "102", "101"]  # newest first
    by_uid = {e["uid"]: e for e in page}
    bank = by_uid["102"]
    assert bank["unread"] is True and bank["from_email"] == "alerts@bank.com"
    assert bank["importance"] == "critical"
    assert "Gmail marked it important" in bank["importance_reasons"]
    assert by_uid["101"]["unread"] is False and "importance" not in by_uid["101"]
    shop = by_uid["103"]
    assert shop["importance"] == "low" and shop["_bulk"] is True
    assert shop["snippet"] == "Hello there"  # html-only: tags stripped, not raw HTML
    # Threading headers travel privately, ready for the cache.
    assert bank["_thread"]["message_id"] == "<m102@x.com>"
    assert bank["_thread"]["references"] == "<root@x.com>"
    # One batched FETCH, a quoted X-GM-RAW query, read-only select, logout.
    fetches = [entry for entry in _ScriptedIMAP.log if entry[:2] == ("uid", "FETCH")]
    assert len(fetches) == 1 and fetches[0][2] == "101,102,103"
    assert "X-GM-LABELS" in fetches[0][3] and "BODY.PEEK[]<0.65536>" in fetches[0][3]
    searches = [entry for entry in _ScriptedIMAP.log if entry[:2] == ("uid", "SEARCH")]
    assert searches[0][2:] == ("X-GM-RAW", '"newer_than:1d"')
    assert ("select", "INBOX", True) in _ScriptedIMAP.log
    assert _ScriptedIMAP.log[-1] == ("logout",)


async def test_fetch_emails_full_body_reads_html_and_lists_to_cc(monkeypatch) -> None:
    monkeypatch.setattr(email_read.imaplib, "IMAP4_SSL", _ScriptedIMAP)
    page = email_read._fetch_one_by_uid("imap.gmail.com", "me@gmail.com", "pw", "103")
    assert len(page) == 1
    item = page[0]
    assert "Hello there" in item["body"]
    assert item["to"] == ["me@gmail.com"] and item["cc"] == []
    assert item["reply_to_email"] == "noreply@shop.com"


async def test_non_gmail_search_quotes_the_text_and_uses_since(monkeypatch) -> None:
    _ScriptedIMAP.log.clear()
    monkeypatch.setattr(email_read.imaplib, "IMAP4_SSL", _ScriptedIMAP)
    email_read._fetch_emails(
        "imap.hostinger.com", "me@omaemirates.com", "pw", 5, "team lunch", "unread", "week",
    )
    searches = [entry for entry in _ScriptedIMAP.log if entry[:2] == ("uid", "SEARCH")]
    assert searches[0][2] == "UNSEEN" and searches[0][3] == "SINCE"
    assert searches[0][5:] == ("TEXT", '"team lunch"')
    fetches = [entry for entry in _ScriptedIMAP.log if entry[:2] == ("uid", "FETCH")]
    assert "X-GM-LABELS" not in fetches[0][3]


async def test_set_seen_by_uid_and_by_newest_match(monkeypatch) -> None:
    _ScriptedIMAP.log.clear()
    monkeypatch.setattr(email_read.imaplib, "IMAP4_SSL", _ScriptedIMAP)
    done = email_read.set_seen("imap.gmail.com", "me@gmail.com", "pw", uid="102")
    assert done == {"count": 1, "uids": ["102"], "subject": "URGENT: payment overdue",
                    "from": "Bank <alerts@bank.com>"}
    assert ("select", "INBOX", False) in _ScriptedIMAP.log
    stores = [entry for entry in _ScriptedIMAP.log if entry[:2] == ("uid", "STORE")]
    assert stores[0][2:] == ("102", "+FLAGS.SILENT", r"(\Seen)")

    _ScriptedIMAP.log.clear()
    done = email_read.set_seen(
        "imap.gmail.com", "me@gmail.com", "pw", query="lunch", seen=False,
    )
    assert done["uids"] == ["103"]  # the newest match, not all of them
    stores = [entry for entry in _ScriptedIMAP.log if entry[:2] == ("uid", "STORE")]
    assert stores[0][3] == "-FLAGS.SILENT"

    done = email_read.set_seen(
        "imap.gmail.com", "me@gmail.com", "pw", category="promotions", all_matching=True,
    )
    assert done["count"] == 3 and "subject" not in done


async def test_fetch_raw_message_and_thread_headers(monkeypatch) -> None:
    monkeypatch.setattr(email_read.imaplib, "IMAP4_SSL", _ScriptedIMAP)
    found = email_read.fetch_raw_message("imap.gmail.com", "me@gmail.com", "pw", uid="101")
    assert found["uid"] == "101" and found["truncated"] is False
    assert b"Team lunch" in found["raw"] and found["size"] > 0
    newest = email_read.fetch_raw_message("imap.gmail.com", "me@gmail.com", "pw", query="x")
    assert newest["uid"] == "103"
    headers = email_read.fetch_thread_headers("imap.gmail.com", "me@gmail.com", "pw", "102")
    assert headers["message_id"] == "<m102@x.com>"
    assert headers["references"] == "<root@x.com>"
    assert headers["subject"] == "URGENT: payment overdue"
    assert headers["reply_to_email"] == "alerts@bank.com"


async def test_read_email_by_uid_returns_a_reply_hint(db_session, monkeypatch) -> None:
    monkeypatch.setattr(email_read.imaplib, "IMAP4_SSL", _ScriptedIMAP)
    ctx = ToolContext(
        session=db_session, email={"address": "me@gmail.com", "appPassword": "pw"},
        email_threads={},
    )
    result = await email_read.ReadEmailTool().run(ctx, account="primary", uid="102")
    assert result["ok"] is True
    assert result["reply_hint"] == {
        "to": "alerts@bank.com", "subject": "Re: URGENT: payment overdue",
        "reply_to_uid": "102",
    }
    assert "Body of URGENT" in result["body"]
    assert ctx.email_threads["me@gmail.com:102"]["message_id"] == "<m102@x.com>"
    assert "takeaways" in result["_instruction"] or "summary" in result["_instruction"]
