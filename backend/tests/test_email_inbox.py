"""inbox_summary (honest counts + triage) and mark_email_read."""

from __future__ import annotations

import imaplib

import pytest

from app.tools import email_inbox, email_read
from app.tools.base import ToolContext
from app.tools.email_inbox import InboxSummaryTool, MarkEmailReadTool
from app.tools.email_read import MailPage

pytestmark = pytest.mark.asyncio

ACCT = {"address": "me@gmail.com", "appPassword": "pw"}


def _ctx(db_session, **over) -> ToolContext:
    return ToolContext(session=db_session, email=ACCT, email_threads={}, **over)


def _page(items, *, total=None, unread_total=None, inbox_total=None,
          inbox_unread=None) -> MailPage:
    page = MailPage(items)
    page.total = len(items) if total is None else total
    page.unread_total = unread_total
    page.inbox_total = inbox_total
    page.inbox_unread = inbox_unread
    return page


def _mail(uid, subject, *, importance=None, score=0, unread=False, bulk=False,
          sender="A <a@x.com>", date="2026-09-12T09:00:00+00:00", reasons=None):
    it = {
        "uid": uid, "from": sender, "from_name": sender.split(" <")[0],
        "from_email": sender.split("<")[-1].rstrip(">"), "subject": subject,
        "date": date, "snippet": "snippet " + subject, "unread": unread,
        "_score": score, "_thread": {"uid": uid, "message_id": f"<{uid}@x>"},
    }
    if importance:
        it["importance"] = importance
    if reasons:
        it["importance_reasons"] = reasons
    if bulk:
        it["_bulk"] = True
    return it


# ---- inbox_summary ----------------------------------------------------------------

async def test_summary_reports_the_true_total_not_the_fetched_count(
    db_session, monkeypatch
) -> None:
    """Twelve came in, ten were fetched → the model is told 'more than 10'."""
    seen: dict = {}

    def fake_fetch(host, address, password, limit, query, category, range_,
                   full_body=False, fetch_bytes=None, with_unread=False):
        seen.update(limit=limit, range_=range_, fetch_bytes=fetch_bytes,
                    with_unread=with_unread)
        items = [_mail(str(i), f"Mail {i}", unread=(i % 2 == 0)) for i in range(10)]
        return _page(items, total=12, unread_total=7, inbox_total=900, inbox_unread=40)

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    result = await InboxSummaryTool().run(_ctx(db_session), account="primary")
    assert result["ok"] is True
    assert result["total"] == 12 and result["fetched"] == 10
    assert result["has_more"] is True
    assert result["unread"] == 7
    assert result["inbox_total"] == 900 and result["inbox_unread"] == 40
    assert "more than 10" in result["_instruction"]
    assert "12" in result["_instruction"]
    assert seen["with_unread"] is True and seen["fetch_bytes"] == email_inbox._SUMMARY_FETCH_BYTES
    assert seen["limit"] == email_inbox._SUMMARY_LIMIT
    assert seen["range_"] == "today"


async def test_summary_ranks_critical_then_important_then_the_rest(
    db_session, monkeypatch
) -> None:
    items = [
        _mail("1", "Newsletter", bulk=True, importance="low", score=-3),
        _mail("2", "URGENT: invoice overdue", importance="critical", score=8,
              reasons=["the subject says 'urgent'"], unread=True),
        _mail("3", "Lunch"),
        _mail("4", "Deadline tomorrow", importance="high", score=4,
              reasons=["it's about 'deadline'"]),
        _mail("5", "Promo", bulk=True, importance="low", score=-2, sender="Shop <s@y.com>"),
        _mail("6", "Promo 2", bulk=True, importance="low", score=-2, sender="Shop <s@y.com>"),
    ]
    monkeypatch.setattr(
        email_read, "_fetch_emails", lambda *a, **k: _page(items, unread_total=1),
    )
    result = await InboxSummaryTool().run(_ctx(db_session), account="primary")
    assert [e["uid"] for e in result["critical"]] == ["2"]
    assert result["critical"][0]["why"] == ["the subject says 'urgent'"]
    assert result["critical"][0]["unread"] is True
    assert "snippet" in result["critical"][0]
    assert [e["uid"] for e in result["important"]] == ["4"]
    assert {e["uid"] for e in result["others"]} == {"1", "3", "5", "6"}
    assert "snippet" not in result["others"][0]
    assert result["newsletters"] == 3
    assert result["top_senders"] == [
        {"name": "A", "count": 4}, {"name": "Shop", "count": 2},
    ]
    assert result["has_more"] is False
    assert "critical and important" in result["_instruction"]
    # No private keys leak to the model.
    assert not any(k.startswith("_") for e in result["critical"] for k in e)


async def test_summary_widens_to_the_week_when_today_is_empty(
    db_session, monkeypatch
) -> None:
    calls: list[str] = []

    def fake_fetch(host, address, password, limit, query, category, range_,
                   full_body=False, **_k):
        calls.append(range_)
        if range_ == "today":
            return _page([], total=0, unread_total=0)
        return _page([_mail("1", "Hi")], unread_total=0)

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    result = await InboxSummaryTool().run(_ctx(db_session), account="primary")
    assert calls == ["today", "week"]
    assert result["range"] == "week" and result["widened_from"] == "today"
    assert result["total"] == 1
    assert "Nothing arrived today" in result["_instruction"]


async def test_summary_does_not_widen_an_explicit_range_or_filter(
    db_session, monkeypatch
) -> None:
    calls: list[str] = []

    def fake_fetch(host, address, password, limit, query, category, range_,
                   full_body=False, **_k):
        calls.append(range_)
        return _page([], total=0, unread_total=0)

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    result = await InboxSummaryTool().run(
        _ctx(db_session), account="primary", range="yesterday",
    )
    assert calls == ["yesterday"] and result["total"] == 0
    assert "Nothing looks urgent" in result["_instruction"]
    calls.clear()
    await InboxSummaryTool().run(_ctx(db_session), account="primary", query="amazon")
    assert calls == ["today"]


async def test_summary_asks_for_the_account_first(db_session, monkeypatch) -> None:
    touched = {"n": 0}
    monkeypatch.setattr(
        email_read, "_fetch_emails", lambda *a, **k: touched.__setitem__("n", 1),
    )
    result = await InboxSummaryTool().run(
        ToolContext(session=db_session, email=ACCT, email_selection={}),
    )
    assert result["status"] == "needs_confirmation" and touched["n"] == 0


async def test_summary_outage_is_not_an_empty_inbox(db_session, monkeypatch) -> None:
    def down(*_a, **_k):
        raise TimeoutError("imap timed out")

    monkeypatch.setattr(email_read, "_fetch_emails", down)
    result = await InboxSummaryTool().run(_ctx(db_session), account="primary")
    assert result["ok"] is False
    assert "empty" in result["message"].lower()


async def test_summary_auth_failure_is_graceful(db_session, monkeypatch) -> None:
    def bad(*_a, **_k):
        raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    monkeypatch.setattr(email_read, "_fetch_emails", bad)
    result = await InboxSummaryTool().run(_ctx(db_session), account="primary")
    assert result["ok"] is False and "password" in result["message"].lower()


async def test_summary_fills_the_thread_cache(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        email_read, "_fetch_emails", lambda *a, **k: _page([_mail("7", "x")], unread_total=0),
    )
    ctx = _ctx(db_session)
    await InboxSummaryTool().run(ctx, account="primary")
    assert ctx.email_threads["me@gmail.com:7"]["message_id"] == "<7@x>"
    assert ctx.email_threads["<7@x>"]["uid"] == "7"


# ---- mark_email_read ---------------------------------------------------------------

async def test_mark_read_by_uid(db_session, monkeypatch) -> None:
    seen: dict = {}

    def fake_set_seen(host, address, password, **kw):
        seen.update(host=host, address=address, **kw)
        return {"count": 1, "uids": ["31"], "subject": "Hi", "from": "A <a@x.com>"}

    monkeypatch.setattr(email_read, "set_seen", fake_set_seen)
    result = await MarkEmailReadTool().run(_ctx(db_session), account="primary", uid="31")
    assert result["ok"] is True
    assert result["marked"] == "read" and result["count"] == 1
    assert result["subject"] == "Hi"
    assert seen["uid"] == "31" and seen["seen"] is True and seen["all_matching"] is False


async def test_mark_unread_by_query(db_session, monkeypatch) -> None:
    seen: dict = {}

    def fake_set_seen(host, address, password, **kw):
        seen.update(**kw)
        return {"count": 1, "uids": ["5"], "subject": "Invoice"}

    monkeypatch.setattr(email_read, "set_seen", fake_set_seen)
    result = await MarkEmailReadTool().run(
        _ctx(db_session), account="primary", query="amazon", unread=True,
    )
    assert result["marked"] == "unread"
    assert seen["query"] == "amazon" and seen["seen"] is False
    assert seen["range_"] == "month"  # a search looks back a month


async def test_mark_all_promotions_read(db_session, monkeypatch) -> None:
    seen: dict = {}

    def fake_set_seen(host, address, password, **kw):
        seen.update(**kw)
        return {"count": 23, "uids": [str(i) for i in range(23)]}

    monkeypatch.setattr(email_read, "set_seen", fake_set_seen)
    result = await MarkEmailReadTool().run(
        _ctx(db_session), account="primary", all=True, category="promotions",
        range="today",
    )
    assert result["ok"] is True and result["count"] == 23
    assert seen["all_matching"] is True and seen["category"] == "promotions"


async def test_mark_read_without_a_target_asks(db_session, monkeypatch) -> None:
    touched = {"n": 0}
    monkeypatch.setattr(
        email_read, "set_seen", lambda *a, **k: touched.__setitem__("n", 1),
    )
    result = await MarkEmailReadTool().run(_ctx(db_session), account="primary")
    assert result["ok"] is False and "which email" in result["message"].lower()
    assert touched["n"] == 0


async def test_mark_read_no_match(db_session, monkeypatch) -> None:
    monkeypatch.setattr(email_read, "set_seen", lambda *a, **k: {"count": 0, "uids": []})
    result = await MarkEmailReadTool().run(
        _ctx(db_session), account="primary", query="unicorn",
    )
    assert result["ok"] is False and "no matching" in result["message"].lower()


async def test_mark_read_needs_the_account_first(db_session, monkeypatch) -> None:
    touched = {"n": 0}
    monkeypatch.setattr(
        email_read, "set_seen", lambda *a, **k: touched.__setitem__("n", 1),
    )
    result = await MarkEmailReadTool().run(
        ToolContext(session=db_session, email=ACCT, email_selection={}), uid="1",
    )
    assert result["status"] == "needs_confirmation" and touched["n"] == 0


async def test_the_orchestrator_shares_one_thread_cache_across_calls() -> None:
    """Threading from a uid only works if every ToolContext gets the SAME dict."""
    import inspect

    from app.agent import orchestrator

    src = inspect.getsource(orchestrator)
    assert "self._email_threads: dict[str, Any] = {}" in src
    assert "email_threads=self._email_threads" in src


async def test_the_summary_names_the_whole_inbox_unread(db_session, monkeypatch) -> None:
    # Device E2.3: "how many unread?" got today's 42, not the inbox's 1402 —
    # the guidance named only the range's unread.
    monkeypatch.setattr(
        email_read, "_fetch_emails",
        lambda *a, **k: _page(
            [_mail("1", "Hi", unread=True)], total=1, unread_total=42,
            inbox_total=5000, inbox_unread=1402,
        ),
    )
    result = await InboxSummaryTool().run(_ctx(db_session), account="primary")
    assert "42 of them unread" in result["_instruction"]
    assert "whole inbox has 1402 unread" in result["_instruction"]
    assert result["inbox_unread"] == 1402


# ---- every mailbox ----------------------------------------------------------------

TWO = [
    {"label": "Personal", "address": "me@gmail.com", "appPassword": "pw", "primary": True},
    {"label": "Work", "address": "me@work.com", "appPassword": "pw", "host": "imap.work.com"},
]


async def test_both_mailboxes_are_summarised_together(db_session, monkeypatch) -> None:
    # Device: "summary of both" had no path, so the model asked "which one?".
    def fake_fetch(host, address, password, *a, **k):
        if "work" in address:
            return _page([_mail("9", "Invoice", importance="critical", reasons=["overdue"])],
                         total=3, unread_total=2, inbox_unread=20)
        return _page([_mail("1", "Hi")], total=5, unread_total=1, inbox_unread=100)

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    ctx = ToolContext(session=db_session, emails=TWO, email_threads={})
    result = await InboxSummaryTool().run(ctx, account="both")
    assert result["ok"] is True and result["account"] == "all"
    assert result["total"] == 8 and result["unread"] == 3
    assert result["inbox_unread"] == 120
    assert [m["account"] for m in result["mailboxes"]] == ["Personal", "Work"]
    assert result["mailboxes"][1]["critical"][0]["subject"] == "Invoice"
    assert "which mailbox" in result["_instruction"]


async def test_one_unreachable_mailbox_is_named_not_hidden(db_session, monkeypatch) -> None:
    def fake_fetch(host, address, password, *a, **k):
        if "work" in address:
            raise OSError("network down")
        return _page([_mail("1", "Hi")], total=1)

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    ctx = ToolContext(session=db_session, emails=TWO, email_threads={})
    result = await InboxSummaryTool().run(ctx, account="all")
    assert result["ok"] is True and result["unreachable_accounts"] == ["Work"]
    assert "Work mailbox could not be read" in result["_instruction"]


async def test_every_mailbox_failing_is_an_outage_not_an_empty_inbox(
    db_session, monkeypatch,
) -> None:
    def down(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(email_read, "_fetch_emails", down)
    ctx = ToolContext(session=db_session, emails=TWO, email_threads={})
    result = await InboxSummaryTool().run(ctx, account="dono")
    assert result["ok"] is False and "do NOT say the inbox is empty" in result["message"]


async def test_the_words_for_every_mailbox() -> None:
    from app.tools.email_accounts import wants_all

    for said in ("all", "both", "Both accounts", "dono", "दोनों", "sab"):
        assert wants_all(said), said
    for said in ("", "primary", "work", "me@gmail.com", "ball"):
        assert not wants_all(said), said


async def test_mark_all_needs_a_named_filter(db_session, monkeypatch) -> None:
    # Review 2026-09-24: all=true alone passed the guard through the default
    # window and would mark every mail of the week read.
    called: list = []
    monkeypatch.setattr(email_read, "set_seen", lambda *a, **k: called.append(k) or {"count": 1})
    result = await MarkEmailReadTool().run(_ctx(db_session), all=True, account="primary")
    assert result["ok"] is False and called == []
    ok = await MarkEmailReadTool().run(
        _ctx(db_session), all=True, category="promotions", account="primary",
    )
    assert ok["ok"] is True and called[0]["category"] == "promotions"


async def test_both_mailboxes_fit_the_model_result(db_session, monkeypatch) -> None:
    many = [_mail(str(i), f"Mail {i}") for i in range(12)]
    monkeypatch.setattr(email_read, "_fetch_emails", lambda *a, **k: _page(many, total=40))
    ctx = ToolContext(session=db_session, emails=TWO, email_threads={})
    result = await InboxSummaryTool().run(ctx, account="all")
    for box in result["mailboxes"]:
        assert "others" not in box and "top_senders" not in box
        assert "others_not_listed" in box


async def test_a_wrong_password_in_one_mailbox_says_so(db_session, monkeypatch) -> None:
    def fake_fetch(host, address, password, *a, **k):
        if "work" in address:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return _page([_mail("1", "Hi")], total=1)

    monkeypatch.setattr(email_read, "_fetch_emails", fake_fetch)
    ctx = ToolContext(session=db_session, emails=TWO, email_threads={})
    result = await InboxSummaryTool().run(ctx, account="all")
    assert "Work mailbox could not be read" in result["_instruction"]
    assert "password" in result["_instruction"].lower()
