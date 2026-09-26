"""``find_email_contact`` tool: a spoken NAME → the email address to use.

Device 2026-09-26: "forward this to Zara Rida", "search Lubna Farooqui and
send" — Farry had no way to look a person's email address up, so the user had
to spell addresses letter by letter over voice, and the spelling kept coming
back wrong. The only contact lookup (``resolve_contact``) finds phone numbers.

Two places know the user's email contacts, and both are searched:

* **the phone's contacts** — which on Android include every Google (Gmail)
  contact synced to the phone, with their email addresses. The phone matches
  the name itself and returns the addresses (``resolve_contact_request`` with
  ``channel: "email"``);
* **the mailboxes themselves** — who wrote to the user (From) and who the user
  wrote to (To / Cc) in every registered account. For a work mailbox (e.g.
  Hostinger) this IS the contact list: there is no address-book API behind
  IMAP.

Read-only. The address found is only ever a proposal: send_email /
forward_email still read the draft back and wait for the user's yes
(``app.tools.email_drafts``).
"""

from __future__ import annotations

import asyncio
import difflib
import email as emaillib
import imaplib
import re
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext
from app.tools.email_accounts import (
    NO_ACCOUNT_MESSAGE,
    _match as match_account,
    usable_accounts,
    wants_all,
)
from app.tools.email_read import (
    _close,
    _decode,
    _fetch_batch,
    _imap_creds,
    _imap_quote,
    _is_gmail,
    _open,
)

logger = get_logger(__name__)

#: Newest messages whose headers are read per search (From in the inbox,
#: To in Sent). Enough for a real correspondent; small enough to stay fast
#: on a 20k-mail inbox.
_MAX_MESSAGES = 40

#: At most this many people are handed to the model; reading more aloud is
#: unusable (see ``app.tools.contacts._MAX_OPTIONS``).
_MAX_OPTIONS = 4

#: Whole budget for the phone's answer.
_DEVICE_TIMEOUT_S = 8.0

#: How close a spoken word must be to a name part to count. Speech-to-text
#: spells names loosely ("Farooqi" for "Farooqui", "Rida" for "Ridha").
_FUZZY = 0.8

#: Senders nobody means when they say a person's name.
_MACHINE = re.compile(
    r"(no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster|"
    r"notification|notifications|bounce|newsletter|alerts?@)",
    re.IGNORECASE,
)

#: Words that are how people address someone, not part of a saved name.
_HONORIFICS = {
    "ji", "bhai", "bhaiya", "sir", "madam", "maam", "sahab", "saheb",
    "sahib", "uncle", "aunty", "auntie", "mr", "mrs", "ms", "dr", "doctor",
    "didi", "baji", "apa",
}


def _terms(name: str) -> list[str]:
    words = [w for w in re.split(r"[\s,._-]+", name.lower()) if w]
    kept = [w for w in words if w not in _HONORIFICS]
    return kept or words


def _parts(display: str, address: str) -> list[str]:
    """The pieces a spoken name can match: display-name words and the
    address's local part split on dots, dashes and digits."""
    local = address.split("@", 1)[0].lower()
    pieces = re.split(r"[\s,._\-+0-9\"']+", f"{display.lower()} {local}")
    return [p for p in pieces if len(p) >= 2]


def _word_matches(term: str, parts: list[str]) -> bool:
    for p in parts:
        if p == term or p.startswith(term) or (len(p) >= 4 and term.startswith(p)):
            return True
        if len(term) >= 4 and difflib.SequenceMatcher(None, term, p).ratio() >= _FUZZY:
            return True
    return False


def matches(name: str, display: str, address: str) -> bool:
    """Every spoken word of ``name`` must match a part of the person."""
    terms = _terms(name)
    if not terms:
        return False
    parts = _parts(display, address)
    return all(_word_matches(t, parts) for t in terms)


def _search_word(name: str) -> str:
    """The one word the mailbox is searched for server-side — the longest,
    since it is the most distinctive; the rest is matched locally."""
    terms = sorted(_terms(name), key=len, reverse=True)
    return terms[0] if terms else ""


def _folders(imap: imaplib.IMAP4_SSL, is_gmail: bool) -> list[tuple[str, str]]:
    """``[(mailbox, header)]`` to search: INBOX by From, Sent by To.

    Gmail's All Mail holds both directions, so one search there covers
    From and To. Other servers get their Sent folder by the SPECIAL-USE
    flag, else by the usual names.
    """
    try:
        typ, data = imap.list()
    except Exception:  # noqa: BLE001
        typ, data = "NO", []
    names: list[tuple[str, str]] = []  # (flags, name)
    for raw in data or []:
        if not isinstance(raw, bytes):
            continue
        line = raw.decode(errors="replace")
        m = re.match(r'\((?P<flags>[^)]*)\)\s+"?[^"\s]*"?\s+(?P<name>.+)$', line)
        if m:
            names.append((m.group("flags"), m.group("name").strip().strip('"')))
    if is_gmail:
        for flags, nm in names:
            if "\\All" in flags:
                return [(nm, "FROM"), (nm, "TO")]
    out = [("INBOX", "FROM")]
    sent = next((nm for flags, nm in names if "\\Sent" in flags), None)
    if sent is None:
        for flags, nm in names:
            if nm.lower().rsplit(".", 1)[-1].rsplit("/", 1)[-1] in (
                "sent", "sent items", "sent messages", "sent mail",
            ):
                sent = nm
                break
    if sent:
        out.append((sent, "TO"))
    return out


def _search_mailbox(host: str, address: str, password: str, name: str
                    ) -> list[dict[str, Any]]:
    """People in one mailbox whose name/address matches ``name``."""
    word = _search_word(name)
    if not word:
        return []
    imap, _total = _open(host, address, password)
    people: dict[str, dict[str, Any]] = {}
    try:
        for folder, header in _folders(imap, _is_gmail(host)):
            try:
                typ, _ = imap.select(_imap_quote(folder), readonly=True)
                if typ != "OK":
                    continue
                typ, data = imap.uid("SEARCH", header, _imap_quote(word))
            except imaplib.IMAP4.error as exc:
                logger.info("email_contacts.search_failed", folder=folder,
                            error=str(exc))
                continue
            if typ != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()[-_MAX_MESSAGES:]
            rows = _fetch_batch(
                imap, uids,
                "(UID BODY.PEEK[HEADER.FIELDS (FROM TO CC DATE)])",
            )
            for _uid, _meta, raw in rows:
                msg = emaillib.message_from_bytes(raw)
                try:
                    when = parsedate_to_datetime(msg.get("Date")).isoformat()
                except Exception:  # noqa: BLE001
                    when = ""
                fields = ["From"] if header == "FROM" else ["To", "Cc"]
                values = [_decode(msg.get(f)) for f in fields if msg.get(f)]
                for disp, addr in getaddresses(values):
                    addr = (addr or "").strip().lower()
                    if "@" not in addr or addr == address.lower():
                        continue
                    if _MACHINE.search(addr):
                        continue
                    disp = (disp or "").strip().strip('"')
                    if not matches(name, disp, addr):
                        continue
                    p = people.setdefault(addr, {
                        "email": addr, "name": disp, "mails": 0, "last": "",
                    })
                    p["mails"] += 1
                    if disp and not p["name"]:
                        p["name"] = disp
                    if when > p["last"]:
                        p["last"] = when
    finally:
        _close(imap)
    return list(people.values())


async def _from_device(ctx: ToolContext, name: str) -> list[dict[str, Any]]:
    """Email addresses of matching people in the phone's contacts."""
    if ctx.resolve_contact is None:
        return []
    try:
        res = await asyncio.wait_for(
            ctx.resolve_contact(name, "email"), timeout=_DEVICE_TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("email_contacts.device_failed", error=repr(exc))
        return []
    out: list[dict[str, Any]] = []
    for c in (res or {}).get("candidates") or []:
        # An older app answers an "email" request with phone matches only;
        # those carry no address and are simply not email contacts.
        for addr in c.get("emails") or ([c["email"]] if c.get("email") else []):
            addr = (addr or "").strip().lower()
            if "@" in addr:
                out.append({
                    "email": addr,
                    "name": (c.get("displayName") or "").strip(),
                    "source": "phone contacts",
                })
    return out


def _merge(device: list[dict[str, Any]],
           mailbox: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """One entry per address: phone contacts first, then by how often the
    user mailed with them, then how recently."""
    people: dict[str, dict[str, Any]] = {}
    for d in device:
        people.setdefault(d["email"], {
            "email": d["email"], "name": d["name"], "found_in": ["phone contacts"],
            "mails": 0, "last": "", "_rank": 0,
        })
    for label, m in mailbox:
        p = people.setdefault(m["email"], {
            "email": m["email"], "name": m["name"], "found_in": [],
            "mails": 0, "last": "", "_rank": 1,
        })
        if not p["name"] and m["name"]:
            p["name"] = m["name"]
        src = f"{label} mailbox"
        if src not in p["found_in"]:
            p["found_in"].append(src)
        p["mails"] += m["mails"]
        if m["last"] > p["last"]:
            p["last"] = m["last"]
    # Newest first, then a stable sort on rank and mail count: recency only
    # breaks ties.
    ranked = sorted(people.values(), key=lambda p: p["last"], reverse=True)
    ranked.sort(key=lambda p: (p["_rank"], -p["mails"]))
    for p in ranked:
        p.pop("_rank", None)
        if not p["last"]:
            p.pop("last")
    return ranked


class FindEmailContactTool(Tool):
    """Look a person's email address up by name — read-only."""

    name = "find_email_contact"
    description = (
        "Find a person's EMAIL ADDRESS by their name — read-only, no "
        "confirmation; call it straight away whenever the user names someone "
        "to email, forward to, reply to or cc ('Lubna ko mail bhejo', 'forward "
        "this to Zara', 'cc Ahmed'). Searches the phone's contacts (which "
        "include the synced Gmail contacts) and the people the user has "
        "emailed with in their mailboxes. Returns found (one person), "
        "ambiguous (a few to choose from), or not_found. Use the returned "
        "address in send_email / forward_email; the draft read-back still "
        "happens there. NEVER guess an address instead of calling this."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The person's name as the user said it.",
            },
            "account": {
                "type": "string",
                "description": "Only search this mailbox ('primary', "
                "'secondary', a label or an address). Omit to search every "
                "mailbox and the phone's contacts.",
            },
        },
        "required": ["name"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        name = (kwargs.get("name") or "").strip()
        if not name:
            return {"ok": True, "status": "not_found",
                    "message": "Ask the user whose email address they mean."}
        account_arg = (kwargs.get("account") or "").strip()
        accts = usable_accounts(ctx)
        if account_arg and not wants_all(account_arg):
            chosen = match_account(account_arg, accts)
            if chosen is not None:
                accts = [chosen]

        async def one(acct: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
            host, address, password, label = _imap_creds(acct)
            try:
                found = await asyncio.to_thread(
                    _search_mailbox, host, address, password, name
                )
                return label, found, None
            except Exception as exc:  # noqa: BLE001
                logger.warning("email_contacts.mailbox_failed", account=label,
                               error=str(exc))
                return label, [], label

        device_task = asyncio.create_task(_from_device(ctx, name))
        results = await asyncio.gather(*(one(a) for a in accts))
        device = await device_task

        mailbox: list[tuple[str, dict[str, Any]]] = []
        failed: list[str] = []
        for label, found, err in results:
            if err:
                failed.append(err)
            mailbox.extend((label, f) for f in found)
        people = _merge(device, mailbox)

        base: dict[str, Any] = {"ok": True, "name": name}
        if failed:
            base["unreachable_accounts"] = failed
        if not people:
            where = "the phone's contacts"
            if accts:
                where += " and the mailboxes"
            elif not ctx.resolve_contact:
                return {"ok": False, "message": NO_ACCOUNT_MESSAGE}
            return {
                **base,
                "status": "not_found",
                "_instruction": (
                    f"No email address for '{name}' in {where}. Say so, and ask "
                    "for another spelling of the name or the full address — "
                    "never make one up."
                    + (f" ({', '.join(failed)} could not be searched.)"
                       if failed else "")
                ),
            }
        if len(people) == 1:
            p = people[0]
            return {
                **base,
                "status": "found",
                "person": p,
                "_instruction": (
                    f"Found {p['name'] or name}: {p['email']}. Use this "
                    "address; the draft read-back will confirm it with the "
                    "user."
                ),
            }
        shown = people[:_MAX_OPTIONS]
        more = len(people) - len(shown)
        return {
            **base,
            "status": "ambiguous",
            "options": shown,
            "more": more,
            "_instruction": (
                "Several people match. Read these out by name with the "
                "address's domain (e.g. 'Lubna Farooqui at gmail, or Lubna "
                "Khan at izylrn') and ask which one; do not pick for them."
                + (f" Mention {more} more, so they can say the full name."
                   if more else "")
            ),
        }
