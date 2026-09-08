"""Which mailbox an email tool uses — and that it never guesses.

Live test 2026-09-08: asked to check email, the assistant read a mailbox the
user had never been asked about. The spec that followed: with no account
registered, say so; with one, name it and get a yes; with two, list Primary
and Secondary and get a choice; keep the choice for the session; never lose
the original request. The tool enforces all of it, so a model that forgets
to ask still cannot read the wrong inbox.
"""

from __future__ import annotations

import pytest

from app.tools import email_read, email_send
from app.tools.base import ToolContext
from app.tools.email_accounts import NO_ACCOUNT_MESSAGE, resolve_account
from app.tools.email_read import ReadEmailsTool, ReadEmailTool
from app.tools.email_send import SendEmailTool

pytestmark = pytest.mark.asyncio

ONE = [{"label": "Personal", "address": "ABC@test.com", "appPassword": "pw", "primary": True}]
TWO = [
    {"label": "Work", "address": "XYZ@test.com", "appPassword": "pw", "primary": False},
    {"label": "Personal", "address": "ABC@test.com", "appPassword": "pw", "primary": True},
]


def _ctx(db_session, emails, memory=None):
    return ToolContext(session=db_session, emails=emails, email_selection=memory)


def _asked() -> dict:
    """A session in which the account question has already been put."""
    return {"asked": True}


# ---- 0 accounts --------------------------------------------------------------

async def test_no_account_is_said_in_the_spec_words(db_session) -> None:
    acct, ask = resolve_account(_ctx(db_session, []), None)
    assert acct is None
    assert ask["status"] == "no_account"
    assert ask["message"] == NO_ACCOUNT_MESSAGE
    assert ask["message"] == (
        "No email account is registered in the app. Please register an account first."
    )


async def test_no_account_even_when_one_is_named(db_session) -> None:
    acct, ask = resolve_account(_ctx(db_session, []), "primary")
    assert acct is None and ask["status"] == "no_account"


# ---- 1 account ---------------------------------------------------------------

async def test_one_account_asks_for_confirmation_first(db_session) -> None:
    acct, ask = resolve_account(_ctx(db_session, ONE, {}), None)
    assert acct is None
    assert ask["status"] == "needs_confirmation"
    assert ask["message"] == (
        "Only one email account is registered: 'ABC@test.com'. Should I "
        "continue with this account?"
    )
    assert "ORIGINAL request" in ask["instructions"]
    assert "pw" not in str(ask), "a password never reaches the model"


async def test_one_account_yes_then_remembered(db_session) -> None:
    memory: dict = _asked()
    # The user said yes → the model names the address.
    acct, ask = resolve_account(_ctx(db_session, ONE, memory), "ABC@test.com")
    assert ask is None and acct["address"] == "ABC@test.com"
    assert memory == {"asked": True, "address": "ABC@test.com"}
    # The next email question needs no asking.
    acct, ask = resolve_account(_ctx(db_session, ONE, memory), None)
    assert ask is None and acct["address"] == "ABC@test.com"


# ---- 2 accounts --------------------------------------------------------------

async def test_two_accounts_list_primary_and_secondary(db_session) -> None:
    acct, ask = resolve_account(_ctx(db_session, TWO, {}), None)
    assert acct is None
    assert ask["status"] == "needs_selection"
    assert ask["message"] == (
        "Both email accounts are registered. Your registered accounts are: "
        "Primary: 'ABC@test.com' and Secondary: 'XYZ@test.com'. Please let me "
        "know which account I can help you with."
    )
    assert [a["role"] for a in ask["accounts"]] == ["Primary", "Secondary"]


@pytest.mark.parametrize(
    "said, expected",
    [
        ("Primary", "ABC@test.com"),
        ("secondary", "XYZ@test.com"),
        ("Use my primary account", "ABC@test.com"),
        ("use my secondary account", "XYZ@test.com"),
        ("ABC@test.com", "ABC@test.com"),
        ("xyz@test.com", "XYZ@test.com"),
        ("Work", "XYZ@test.com"),
        ("the work one", "XYZ@test.com"),
    ],
)
async def test_two_accounts_every_way_of_naming_one(db_session, said, expected) -> None:
    memory: dict = _asked()
    acct, ask = resolve_account(_ctx(db_session, TWO, memory), said)
    assert ask is None, ask
    assert acct["address"] == expected
    assert memory["address"] == expected


async def test_two_accounts_unclear_answer_is_asked_again(db_session) -> None:
    memory: dict = _asked()
    acct, ask = resolve_account(_ctx(db_session, TWO, memory), "the blue one")
    assert acct is None
    assert ask["status"] == "needs_selection"
    assert ask["message"] == (
        "Please specify whether you want me to use your Primary account "
        "(ABC@test.com) or Secondary account (XYZ@test.com)."
    )
    assert "address" not in memory, "an unclear answer settles nothing"


async def test_selection_is_kept_for_later_questions(db_session) -> None:
    memory: dict = _asked()
    resolve_account(_ctx(db_session, TWO, memory), "secondary")
    acct, ask = resolve_account(_ctx(db_session, TWO, memory), None)
    assert ask is None and acct["address"] == "XYZ@test.com"


async def test_a_remembered_mailbox_that_vanished_is_asked_afresh(db_session) -> None:
    memory = {"address": "gone@test.com", "asked": True}
    acct, ask = resolve_account(_ctx(db_session, TWO, memory), None)
    assert acct is None and ask["status"] == "needs_selection"
    assert "address" not in memory


# ---- the tools obey it -------------------------------------------------------

async def test_read_emails_does_not_touch_imap_before_confirmation(
    db_session, monkeypatch
) -> None:
    touched = {"n": 0}

    def fetch(*_a, **_k):
        touched["n"] += 1
        return []

    monkeypatch.setattr(email_read, "_fetch_emails", fetch)
    result = await ReadEmailsTool().run(_ctx(db_session, ONE, {}))
    assert result["status"] == "needs_confirmation"
    assert touched["n"] == 0


async def test_read_email_does_not_touch_imap_before_selection(
    db_session, monkeypatch
) -> None:
    touched = {"n": 0}

    def fetch(*_a, **_k):
        touched["n"] += 1
        return []

    monkeypatch.setattr(email_read, "_fetch_emails", fetch)
    result = await ReadEmailTool().run(_ctx(db_session, TWO, {}), query="John")
    assert result["status"] == "needs_selection"
    assert touched["n"] == 0


async def test_send_email_does_not_send_before_selection(db_session, monkeypatch) -> None:
    sent = {"n": 0}

    def send(*_a, **_k):
        sent["n"] += 1

    monkeypatch.setattr(email_send, "_send", send)
    result = await SendEmailTool().run(_ctx(db_session, TWO, {}), to="a@b.com", body="hi")
    assert result["status"] == "needs_selection"
    assert sent["n"] == 0


async def test_only_the_chosen_mailbox_is_read(db_session, monkeypatch) -> None:
    """Spec §7: the other account's mail is never fetched."""
    seen: list[str] = []

    def fetch(host, address, *_a, **_k):
        seen.append(address)
        return []

    monkeypatch.setattr(email_read, "_fetch_emails", fetch)
    memory: dict = _asked()
    ctx = _ctx(db_session, TWO, memory)
    first = await ReadEmailsTool().run(ctx, account="secondary")
    assert first["ok"] is True
    # A later question without naming it: still only the chosen one.
    await ReadEmailsTool().run(_ctx(db_session, TWO, memory))
    assert seen == ["XYZ@test.com", "XYZ@test.com"]


async def test_the_orchestrator_shares_one_memory_across_calls() -> None:
    """The remembering only works if every ToolContext gets the SAME dict."""
    import inspect

    from app.agent import orchestrator

    src = inspect.getsource(orchestrator)
    assert "self._email_selection: dict[str, Any] = {}" in src
    assert "email_selection=self._email_selection" in src


async def test_a_named_account_counts_only_after_the_session_has_asked(db_session) -> None:
    """Device-seen 2026-09-08: a fresh session, and the model filled in the
    address it remembered from an earlier conversation. The question comes
    first, every session; only then is a named account honoured."""
    memory: dict = {}
    acct, ask = resolve_account(_ctx(db_session, ONE, memory), "ABC@test.com")
    assert acct is None and ask["status"] == "needs_confirmation"
    assert memory == {"asked": True}
    acct, ask = resolve_account(_ctx(db_session, ONE, memory), "ABC@test.com")
    assert ask is None and acct["address"] == "ABC@test.com"

    memory = {}
    acct, ask = resolve_account(_ctx(db_session, TWO, memory), "secondary")
    assert acct is None and ask["status"] == "needs_selection"
    acct, ask = resolve_account(_ctx(db_session, TWO, memory), "secondary")
    assert ask is None and acct["address"] == "XYZ@test.com"
