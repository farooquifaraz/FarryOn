"""``read_emails`` tool: read the user's mail over IMAP, with smart filters.

The user supplies their email address + an app-specific password (e.g. a Gmail
App Password) in the app settings; the client sends it in the ``hello`` so it
lives only for the session and is never persisted server-side. IMAP is
read-only here — we only fetch headers and a short snippet.

Smart filtering is done **server-side** for performance: on Gmail we use the
``X-GM-RAW`` search extension (the same query language as the Gmail search box)
so categories / unread / important / date-ranges are resolved by Gmail and we
only fetch the matched messages. On non-Gmail IMAP we fall back to standard
``SINCE`` / ``UNSEEN`` search.
"""

from __future__ import annotations

import asyncio
import email as emaillib
import imaplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any

from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext

logger = get_logger(__name__)

_DEFAULT_HOST = "imap.gmail.com"
_MAX_LIMIT = 25
_SNIPPET_CHARS = 200

# Gmail X-GM-RAW fragments for each category / filter the user can ask for.
_CATEGORY_GMAIL = {
    "promotions": "category:promotions",
    "social": "category:social",
    "updates": "category:updates",
    "forums": "category:forums",
    "primary": "category:primary",
    "important": "is:important",
    "unread": "is:unread",
    "starred": "is:starred",
}
_RANGE_GMAIL = {
    "today": "newer_than:1d",
    "yesterday": "newer_than:2d older_than:1d",
    "week": "newer_than:7d",
    "month": "newer_than:30d",
}
_RANGE_DAYS = {"today": 1, "yesterday": 2, "week": 7, "month": 30}


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:  # noqa: BLE001
        return value.strip()


def _snippet(msg: emaillib.message.Message) -> str:
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    text = payload.decode(
                        part.get_content_charset() or "utf-8", "replace"
                    )
                    return " ".join(text.split())[:_SNIPPET_CHARS]
            return ""
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
        return " ".join(text.split())[:_SNIPPET_CHARS]
    except Exception:  # noqa: BLE001
        return ""


def _gmail_query(category: str | None, range_: str | None, text: str | None) -> str:
    """Build a Gmail search string from category + range + free text."""
    parts: list[str] = []
    if category and category in _CATEGORY_GMAIL:
        parts.append(_CATEGORY_GMAIL[category])
    if range_ and range_ in _RANGE_GMAIL:
        parts.append(_RANGE_GMAIL[range_])
    elif not range_:
        # Default window: a week when a category is set (so there's something to
        # show), otherwise just today.
        parts.append("newer_than:7d" if category else "newer_than:1d")
    if text:
        parts.append(text.strip())
    return " ".join(parts) or "newer_than:1d"


def _imap_search_args(category: str | None, range_: str | None,
                      text: str | None) -> list[str]:
    """Standard-IMAP fallback search (non-Gmail) for category/range/text."""
    days = _RANGE_DAYS.get(range_ or "today", 1)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%d-%b-%Y"
    )
    args: list[str] = ["SINCE", since]
    if category == "unread":
        args.insert(0, "UNSEEN")
    elif category == "starred" or category == "important":
        args.insert(0, "FLAGGED")
    if text:
        args += ["TEXT", text]
    return args


def _fetch_emails(
    host: str,
    address: str,
    password: str,
    limit: int,
    query: str | None,
    category: str | None,
    range_: str | None,
) -> list[dict[str, Any]]:
    """Blocking IMAP read of the matching messages (run in a thread)."""
    is_gmail = "gmail" in host or "google" in host
    imap = imaplib.IMAP4_SSL(host)
    try:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)

        ids: list[bytes] = []
        gmail_ok = False
        if is_gmail:
            gq = _gmail_query(category, range_, query)
            # The X-GM-RAW query must be sent as a single QUOTED string —
            # imaplib does not quote it, so a multi-word query like
            # "category:promotions newer_than:7d" would otherwise be split into
            # extra tokens and Gmail rejects it (the bogus "couldn't sign in").
            try:
                typ, data = imap.search(None, "X-GM-RAW", f'"{gq}"')
                if typ == "OK":
                    gmail_ok = True
                    if data and data[0]:
                        ids = data[0].split()
            except imaplib.IMAP4.error as exc:
                logger.warning("read_emails.xgmraw_failed", q=gq, error=str(exc))
        if not gmail_ok and not ids:
            # Fallback (non-Gmail, or X-GM-RAW errored).
            typ, data = imap.search(
                None, *_imap_search_args(category, range_, query)
            )
            if typ == "OK" and data and data[0]:
                ids = data[0].split()
        if not ids:
            return []

        ids = ids[-limit:]
        out: list[dict[str, Any]] = []
        for mid in reversed(ids):  # newest first
            typ, msg_data = imap.fetch(mid, "(RFC822)")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = emaillib.message_from_bytes(msg_data[0][1])
            try:
                dt = parsedate_to_datetime(msg.get("Date"))
                when = dt.isoformat() if dt else None
            except Exception:  # noqa: BLE001
                when = None
            out.append(
                {
                    "from": _decode(msg.get("From")),
                    "subject": _decode(msg.get("Subject")),
                    "date": when,
                    "snippet": _snippet(msg),
                }
            )
        return out
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass


class ReadEmailsTool(Tool):
    """Read the user's emails over IMAP with optional category/date filters."""

    name = "read_emails"
    description = (
        "Read the user's emails (sender, subject, snippet). Filter by category "
        "(promotions, social, updates, important, unread, starred, primary) "
        "and/or a time range (today, yesterday, week, month). Use for any "
        "question about their inbox / mail."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": [
                    "promotions", "social", "updates", "forums", "primary",
                    "important", "unread", "starred",
                ],
                "description": "Which kind of emails to read.",
            },
            "range": {
                "type": "string",
                "enum": ["today", "yesterday", "week", "month"],
                "description": "Time window (default today).",
            },
            "query": {
                "type": "string",
                "description": "Optional sender or keyword to filter by.",
            },
            "limit": {
                "type": "integer",
                "description": "How many emails to read (default 10).",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        cfg = ctx.email or {}
        address = (cfg.get("address") or "").strip()
        password = (cfg.get("appPassword") or "").strip()
        host = (cfg.get("host") or _DEFAULT_HOST).strip() or _DEFAULT_HOST
        if not address or not password:
            return {
                "ok": False,
                "message": (
                    "No email is configured. Ask the user to add their email "
                    "address and app password in Settings."
                ),
            }
        try:
            limit = max(1, min(int(kwargs.get("limit") or 10), _MAX_LIMIT))
        except (TypeError, ValueError):
            limit = 10
        category = (kwargs.get("category") or None)
        range_ = (kwargs.get("range") or None)
        query = (kwargs.get("query") or None)
        try:
            emails = await asyncio.to_thread(
                _fetch_emails, host, address, password, limit, query,
                category, range_,
            )
        except imaplib.IMAP4.error as exc:
            logger.warning("read_emails.auth_failed", error=str(exc))
            return {
                "ok": False,
                "message": (
                    "Couldn't sign in to email. Check the address and app "
                    "password in Settings."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("read_emails.failed", error=str(exc))
            return {"ok": False, "message": "Couldn't read email right now."}
        return {
            "ok": True,
            "count": len(emails),
            "category": category,
            "range": range_ or "today",
            "emails": emails,
        }
