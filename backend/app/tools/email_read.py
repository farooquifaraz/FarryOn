"""``read_emails`` tool: read the user's recent mail over IMAP.

The user supplies their email address + an app-specific password (e.g. a Gmail
App Password) in the app settings; the client sends it in the ``hello`` so it
lives only for the session and is never persisted server-side. IMAP is
read-only here — we only fetch headers and a short snippet so the assistant can
answer "what emails did I get today?".
"""

from __future__ import annotations

import asyncio
import email as emaillib
import imaplib
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any

from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext

logger = get_logger(__name__)

_DEFAULT_HOST = "imap.gmail.com"
_MAX_LIMIT = 25
_SNIPPET_CHARS = 200


def _decode(value: str | None) -> str:
    """Decode an RFC 2047 encoded header into plain text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:  # noqa: BLE001 - never fail a read over a bad header
        return value.strip()


def _snippet(msg: emaillib.message.Message) -> str:
    """Best-effort short text snippet from a message body."""
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


def _fetch_emails(
    host: str, address: str, password: str, limit: int, query: str | None
) -> list[dict[str, Any]]:
    """Blocking IMAP read of today's most-recent messages (run in a thread)."""
    imap = imaplib.IMAP4_SSL(host)
    try:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)
        # Today, in the IMAP date format (e.g. 01-Jul-2026).
        since = datetime.now(timezone.utc).strftime("%d-%b-%Y")
        criteria: list[str] = ["SINCE", since]
        if query:
            criteria += ["TEXT", query]
        typ, data = imap.search(None, *criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()[-limit:]  # most recent N
        out: list[dict[str, Any]] = []
        for mid in reversed(ids):  # newest first
            typ, msg_data = imap.fetch(mid, "(RFC822)")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = emaillib.message_from_bytes(msg_data[0][1])
            when = None
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
    """Read the user's recent (today's) emails over IMAP."""

    name = "read_emails"
    description = (
        "Read the user's most recent emails from today (subject, sender, and a "
        "short snippet). Use when the user asks about their email / inbox / "
        "messages they received."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many recent emails to read (default 10).",
            },
            "query": {
                "type": "string",
                "description": "Optional text to filter emails by.",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Read today's emails using the session's IMAP credentials."""
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
        limit = kwargs.get("limit") or 10
        try:
            limit = max(1, min(int(limit), _MAX_LIMIT))
        except (TypeError, ValueError):
            limit = 10
        query = kwargs.get("query") or None
        try:
            emails = await asyncio.to_thread(
                _fetch_emails, host, address, password, limit, query
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
        except Exception as exc:  # noqa: BLE001 - network/parse must not crash turn
            logger.warning("read_emails.failed", error=str(exc))
            return {"ok": False, "message": "Couldn't read email right now."}
        return {"ok": True, "count": len(emails), "emails": emails}
