"""``find_email_contact``: a spoken name → the email address to use.

Device 2026-09-26: "forward this to Zara Rida" / "search Lubna Farooqui and
send" had no tool behind them, so the user spelled addresses letter by letter
and speech-to-text kept getting them wrong. Pinned here: the phone's contacts
and both mailboxes are searched, a loosely-spelled name still matches, robots
and the user's own address never come back, several people are offered (not
picked), and nothing is ever invented.
"""

from __future__ import annotations

import pytest

from app.tools import email_contacts
from app.tools.base import ToolContext
from app.tools.email_contacts import FindEmailContactTool, matches

pytestmark = pytest.mark.asyncio

_GMAIL = {"address": "me@gmail.com", "appPassword": "pw", "label": "Email",
          "primary": True}
_WORK = {"address": "me@izylrn.com", "appPassword": "pw", "label": "Work",
         "host": "imap.hostinger.com"}


def _mail(frm: str, to: str, date: str = "Fri, 25 Sep 2026 10:00:00 +0400",
          cc: str = "") -> bytes:
    lines = [f"From: {frm}", f"To: {to}", f"Date: {date}"]
    if cc:
        lines.append(f"Cc: {cc}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class _FakeImap:
    """Just enough IMAP: LIST, SELECT, UID SEARCH, UID FETCH."""

    def __init__(self, folders: dict[str, dict[str, list[bytes]]],
                 flags: dict[str, str]) -> None:
        # folders[name][header] = raw messages a search on that header finds
        self.folders = folders
        self.flags = flags
        self.current = ""
        self.searches: list[str] = []

    def list(self):
        return "OK", [
            f'({self.flags.get(n, "")}) "/" "{n}"'.encode() for n in self.folders
        ]

    def select(self, name, readonly=True):
        self.current = name.strip('"')
        return ("OK", [b"1"]) if self.current in self.folders else ("NO", [])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            # Like a real server: an exact, case-insensitive substring of
            # that header — a misspelled word finds nothing.
            header, word = args[0], args[1].strip('"').lower()
            self.searches.append(word)
            msgs = self.folders[self.current].get(header, [])
            field = "from:" if header == "FROM" else "to:"
            ids = [
                str(i + 1) for i, raw in enumerate(msgs)
                if any(word in ln.lower() for ln in raw.decode().split("\r\n")
                       if ln.lower().startswith(field))
            ]
            self._msgs = msgs
            return "OK", [" ".join(ids).encode()]
        if cmd == "FETCH":
            out = []
            for uid in args[0].split(","):
                raw = self._msgs[int(uid) - 1]
                out.append((f"{uid} (UID {uid} BODY[HEADER] {{{len(raw)}}}".encode(), raw))
                out.append(b")")
            return "OK", out
        raise AssertionError(cmd)

    def logout(self):
        pass


def _serve(monkeypatch, boxes: dict[str, _FakeImap]) -> None:
    def fake_open(host, address, password, readonly=True):
        if address not in boxes:
            raise OSError("unreachable")
        return boxes[address], 10

    monkeypatch.setattr(email_contacts, "_open", fake_open)
    monkeypatch.setattr(email_contacts, "_close", lambda imap: None)


def _ctx(db, device=None, accounts=(_GMAIL, _WORK)) -> ToolContext:
    async def resolve(name, channel):
        assert channel == "email"
        return device or {"status": "not_found", "candidates": []}

    return ToolContext(session=db, emails=list(accounts),
                       resolve_contact=resolve if device is not None else None)


# --- name matching ----------------------------------------------------------

async def test_a_loosely_spelled_name_still_matches() -> None:
    assert matches("Lubna Farooqi", "Lubna Farooqui", "lubna.f@gmail.com")
    assert matches("zara rida", "", "zara.rida@gmail.com")
    assert matches("Ahmed bhai", "Ahmed Ali", "ahmed@x.com")
    assert not matches("Lubna Khan", "Lubna Farooqui", "lubna.f@gmail.com")
    assert not matches("Sara", "Zara Rida", "zara.rida@gmail.com")


# --- the tool ---------------------------------------------------------------

async def test_gmail_all_mail_and_work_sent_are_both_searched(
    db_session, monkeypatch
) -> None:
    gmail = _FakeImap(
        {"INBOX": {}, "[Gmail]/All Mail": {
            "FROM": [_mail('"Lubna Farooqui" <lubna.f@gmail.com>', "me@gmail.com")],
            "TO": [_mail("me@gmail.com", '"Lubna Farooqui" <lubna.f@gmail.com>')],
        }},
        {"[Gmail]/All Mail": "\\All \\HasNoChildren"},
    )
    work = _FakeImap(
        {"INBOX": {"FROM": []}, "Sent": {
            "TO": [_mail("me@izylrn.com", "Lubna Khan <lubna@izylrn.com>")],
        }},
        {"Sent": "\\Sent"},
    )
    _serve(monkeypatch, {"me@gmail.com": gmail, "me@izylrn.com": work})
    out = await FindEmailContactTool().run(_ctx(db_session), name="Lubna Farooqi")
    assert out["status"] == "found"
    assert out["person"]["email"] == "lubna.f@gmail.com"
    assert out["person"]["mails"] == 2
    assert out["person"]["found_in"] == ["Email mailbox"]

    out = await FindEmailContactTool().run(_ctx(db_session), name="Lubna")
    assert out["status"] == "ambiguous"
    assert {o["email"] for o in out["options"]} == {
        "lubna.f@gmail.com", "lubna@izylrn.com",
    }
    assert "ask which" in out["_instruction"]


async def test_phone_contacts_come_first_and_merge_with_the_mailbox(
    db_session, monkeypatch
) -> None:
    gmail = _FakeImap(
        {"INBOX": {"FROM": [
            _mail("Zara Rida <zara.rida@gmail.com>", "me@gmail.com"),
            _mail("Zara Rida <zara.rida@gmail.com>", "me@gmail.com"),
            _mail("Zara Office <zara@work.com>", "me@gmail.com"),
        ]}},
        {},
    )
    _serve(monkeypatch, {"me@gmail.com": gmail})
    device = {"status": "found", "candidates": [
        {"displayName": "Zara Rida", "emails": ["Zara.Rida@gmail.com"]},
    ]}
    out = await FindEmailContactTool().run(
        _ctx(db_session, device=device, accounts=(_GMAIL,)), name="Zara Rida"
    )
    assert out["status"] == "found"
    p = out["person"]
    assert p["email"] == "zara.rida@gmail.com"
    assert p["found_in"] == ["phone contacts", "Email mailbox"]


async def test_robots_and_the_users_own_address_never_come_back(
    db_session, monkeypatch
) -> None:
    gmail = _FakeImap(
        {"INBOX": {"FROM": [
            _mail("DHL <noreply-dhl@dhl.com>", "me@gmail.com"),
            _mail("DHL Notifications <notifications@dhl.com>", "me@gmail.com"),
        ], "TO": []}},
        {},
    )
    _serve(monkeypatch, {"me@gmail.com": gmail})
    out = await FindEmailContactTool().run(
        _ctx(db_session, accounts=(_GMAIL,)), name="DHL"
    )
    assert out["status"] == "not_found"
    assert "never make one up" in out["_instruction"]


async def test_an_old_app_answering_with_phone_numbers_is_ignored(
    db_session, monkeypatch
) -> None:
    _serve(monkeypatch, {"me@gmail.com": _FakeImap({"INBOX": {}}, {})})
    device = {"status": "found", "candidates": [
        {"contactId": "c1", "displayName": "Lubna", "maskedNumber": "+97•••12"},
    ]}
    out = await FindEmailContactTool().run(
        _ctx(db_session, device=device, accounts=(_GMAIL,)), name="Lubna"
    )
    assert out["status"] == "not_found"


async def test_a_mailbox_that_fails_is_named_and_the_rest_still_answers(
    db_session, monkeypatch
) -> None:
    gmail = _FakeImap(
        {"INBOX": {"FROM": [_mail("Ali <ali@x.com>", "me@gmail.com")]}}, {},
    )
    _serve(monkeypatch, {"me@gmail.com": gmail})  # work mailbox unreachable
    out = await FindEmailContactTool().run(_ctx(db_session), name="Ali")
    assert out["status"] == "found"
    assert out["unreachable_accounts"] == ["Work"]


async def test_one_named_account_limits_the_search(db_session, monkeypatch) -> None:
    gmail = _FakeImap(
        {"INBOX": {"FROM": [_mail("Ali <ali@x.com>", "me@gmail.com")]}}, {},
    )
    work = _FakeImap(
        {"INBOX": {"FROM": [_mail("Ali Work <ali@izylrn.com>", "me@izylrn.com")]}},
        {},
    )
    _serve(monkeypatch, {"me@gmail.com": gmail, "me@izylrn.com": work})
    out = await FindEmailContactTool().run(
        _ctx(db_session), name="Ali", account="secondary"
    )
    assert out["status"] == "found"
    assert out["person"]["email"] == "ali@izylrn.com"


async def test_no_name_asks_who(db_session) -> None:
    out = await FindEmailContactTool().run(_ctx(db_session), name="  ")
    assert out["status"] == "not_found"


async def test_a_misspelled_surname_still_finds_the_person(
    db_session, monkeypatch
) -> None:
    # Device 2026-09-26: "Lubna Faruqi" found nobody — the mailbox was only
    # searched for "faruqi", which no header contains.
    gmail = _FakeImap(
        {"INBOX": {"FROM": [
            _mail('"Lubna Farooqui" <lubna.f@live.com>', "me@gmail.com"),
            _mail('"Lubna Khan" <lkhan@x.com>', "me@gmail.com"),
        ]}},
        {},
    )
    _serve(monkeypatch, {"me@gmail.com": gmail})
    out = await FindEmailContactTool().run(
        _ctx(db_session, accounts=(_GMAIL,)), name="Lubna Faruqi"
    )
    assert out["status"] == "found"
    assert out["person"]["email"] == "lubna.f@live.com"
    assert set(gmail.searches) == {"lubna", "faruqi"}


async def test_the_phone_is_asked_again_for_the_first_name(db_session) -> None:
    asked: list[str] = []

    async def resolve(name, channel):
        asked.append(name)
        if name == "lubna":
            return {"status": "ambiguous", "candidates": [
                {"displayName": "Lubna Farooqui", "emails": ["lubna.f@live.com"]},
                {"displayName": "Lubna Khan", "emails": ["lkhan@x.com"]},
            ]}
        return {"status": "not_found", "candidates": []}

    ctx = ToolContext(session=db_session, emails=[], resolve_contact=resolve)
    out = await FindEmailContactTool().run(ctx, name="Lubna Faruqi")
    assert asked == ["Lubna Faruqi", "lubna"]
    assert out["status"] == "found"
    assert out["person"]["email"] == "lubna.f@live.com"
