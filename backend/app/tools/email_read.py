"""``read_emails`` / ``read_email`` tools: read the user's mail over IMAP.

The user supplies their email address + an app-specific password (e.g. a Gmail
App Password) in the app settings; the client sends it in the ``hello`` so it
lives only for the session and is never persisted server-side. Reading is
strictly read-only here — headers, flags and a bounded body prefix.

Smart filtering is done **server-side** for performance: on Gmail we use the
``X-GM-RAW`` search extension (the same query language as the Gmail search box)
so categories / unread / important / date-ranges are resolved by Gmail and we
only fetch the matched messages. On non-Gmail IMAP we fall back to standard
``SINCE`` / ``UNSEEN`` search.

Everything is addressed by IMAP **UID** (stable for the life of the mailbox),
so a ``uid`` the assistant read out in one call can be handed to
``mark_email_read`` / ``forward_email`` / ``send_email(reply_to_uid)`` in the
next — sequence numbers would shift the moment a mail arrived. The same
module exposes the small IMAP helpers those tools need (:func:`set_seen`,
:func:`fetch_raw_message`, :func:`fetch_thread_headers`) so there is exactly
one IMAP connection path to get right.
"""

from __future__ import annotations

import asyncio
import email as emaillib
import html
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any

from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext
from app.tools.email_accounts import (
    NO_ACCOUNT_MESSAGE,
    resolve_account,
    usable_accounts,
)
from app.tools.email_triage import score_importance

logger = get_logger(__name__)

_DEFAULT_HOST = "imap.gmail.com"

#: Socket timeout for every IMAP operation. Without one, imaplib waits
#: FOREVER: a black-holed server (typo'd custom host, firewall, the user's
#: flaky Wi-Fi) parked the worker thread permanently — the tool's own timeout
#: answered the user, but the thread never came back, and each retry parked
#: another one.
_IMAP_TIMEOUT_S = 15

#: List-view messages are fetched as the FIRST 64 KB of the raw message
#: (``BODY.PEEK[]<0.65536>``) instead of the full RFC822. Headers + enough
#: body for a snippet always fit; a newsletter with a 10 MB attachment no
#: longer costs 10 MB × N messages per "what's in my inbox".
_LIST_FETCH_BYTES = 65536

#: A message bigger than this is forwarded WITHOUT its attachments (the body
#: prefix still goes) — pulling 40 MB through the realtime server for one
#: voice command is not worth the stall.
_MAX_FORWARD_BYTES = 25 * 1024 * 1024

#: "Mark all as read" is capped so a runaway filter can't touch a whole
#: mailbox of history in one go.
_MAX_STORE = 500

#: Thread headers remembered per session (see :func:`remember_threads`).
_THREAD_CACHE_MAX = 200


def _imap_creds(account: dict[str, Any]) -> tuple[str, str, str, str]:
    """``(host, address, password, label)`` for one mailbox dict."""
    address = (account.get("address") or "").strip()
    password = (account.get("appPassword") or "").strip()
    host = (account.get("host") or _DEFAULT_HOST).strip() or _DEFAULT_HOST
    return host, address, password, (account.get("label") or address)


_MAX_LIMIT = 25
_SNIPPET_CHARS = 200
_BODY_CHARS = 4000  # cap the full body so a turn stays manageable

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
#: The windows a tool accepts; ``all`` is the whole mailbox (searches only).
_RANGES = ["today", "yesterday", "week", "month", "all"]


class MailPage(list):
    """The fetched messages plus what the mailbox said about the whole match.

    A plain ``list`` of item dicts (so every caller and test that expects a
    list keeps working) carrying the counts the assistant needs to be honest
    about volume: ``total`` is how many messages MATCHED the search before the
    ``limit`` was applied — the difference between "you got ten emails" and
    "you got more than ten".
    """

    total: int = 0
    inbox_total: int | None = None
    inbox_unread: int | None = None
    #: Unread messages within the searched range (only when asked for).
    unread_total: int | None = None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:  # noqa: BLE001
        return value.strip()


def _snippet(msg: emaillib.message.Message) -> str:
    """The first words of the body — plain text preferred, HTML stripped."""
    try:
        html_part: emaillib.message.Message | None = None
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    return " ".join(_decode_payload(part).split())[:_SNIPPET_CHARS]
                if ct == "text/html" and html_part is None:
                    html_part = part
        elif msg.get_content_type() == "text/html":
            html_part = msg
        else:
            return " ".join(_decode_payload(msg).split())[:_SNIPPET_CHARS]
        if html_part is None:
            return ""
        return " ".join(_html_to_text(_decode_payload(html_part)).split())[:_SNIPPET_CHARS]
    except Exception:  # noqa: BLE001
        return ""


def _html_to_text(s: str) -> str:
    """Crude HTML → readable text for emails that only ship an HTML body."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = re.sub(r"(?is)</p\s*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return html.unescape(s)


def _decode_payload(part: emaillib.message.Message) -> str:
    payload = part.get_payload(decode=True) or b""
    return payload.decode(part.get_content_charset() or "utf-8", "replace")


def _full_body(msg: emaillib.message.Message, limit: int = _BODY_CHARS) -> str:
    """Best-effort full plain-text body (prefers text/plain, else strips HTML)."""
    text: str | None = None
    html_body: str | None = None
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if part.get_content_disposition() == "attachment":
                    continue
                if ct == "text/plain" and text is None:
                    text = _decode_payload(part)
                elif ct == "text/html" and html_body is None:
                    html_body = _decode_payload(part)
        elif msg.get_content_type() == "text/html":
            html_body = _decode_payload(msg)
        else:
            text = _decode_payload(msg)
    except Exception:  # noqa: BLE001
        return ""
    body = text if text else (_html_to_text(html_body) if html_body else "")
    # Collapse blank-line runs and space runs (stripped tags leave gaps),
    # trim each line.
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in body.splitlines()]
    body = "\n".join(ln for ln in lines if ln)
    return body[:limit]


def _attachments(msg: emaillib.message.Message) -> list[dict[str, Any]]:
    """``[{name, size, type}]`` for every attachment part (payloads not kept)."""
    out: list[dict[str, Any]] = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        if part.get_content_disposition() != "attachment" and not part.get_filename():
            continue
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            payload = b""
        out.append({
            "name": _decode(part.get_filename()) or "attachment",
            "size": len(payload),
            "type": part.get_content_type(),
        })
    return out


def _imap_quote(s: str) -> str:
    """An IMAP quoted-string: wrap in double quotes, escaping ``\\`` and ``"``."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _default_range(category: str | None, text: str | None) -> str:
    """The window when the caller named none.

    A SEARCH ("find the email from Faraz") looks back a month — the user is
    hunting for something, and "nothing today" is the wrong answer when it
    arrived on Tuesday. A category browse gets a week so there is something
    to show; a plain "what's new" is just today.
    """
    if text:
        return "month"
    return "week" if category else "today"


def _gmail_query(category: str | None, range_: str | None, text: str | None) -> str:
    """Build a Gmail search string from category + range + free text.

    ``range_="all"`` drops the time fragment: the whole mailbox. ``text`` is
    passed through verbatim, so Gmail's own search syntax works (``from:ali``,
    ``subject:invoice``, ``has:attachment``, ``older_than:1y``).
    """
    parts: list[str] = []
    if category and category in _CATEGORY_GMAIL:
        parts.append(_CATEGORY_GMAIL[category])
    range_ = range_ or _default_range(category, text)
    if range_ in _RANGE_GMAIL:
        parts.append(_RANGE_GMAIL[range_])
    if text:
        parts.append(text.strip())
    return " ".join(parts) or "newer_than:1d"


def _imap_search_args(category: str | None, range_: str | None,
                      text: str | None) -> list[str]:
    """Standard-IMAP fallback search (non-Gmail) for category/range/text."""
    args: list[str] = []
    range_ = range_ or _default_range(category, text)
    if range_ in _RANGE_DAYS:
        since = (
            datetime.now(timezone.utc) - timedelta(days=_RANGE_DAYS[range_])
        ).strftime("%d-%b-%Y")
        args += ["SINCE", since]
    if category == "unread":
        args.insert(0, "UNSEEN")
    elif category == "starred" or category == "important":
        args.insert(0, "FLAGGED")
    if text:
        # Quoted, or a multi-word keyword is split into bogus search keys.
        args += ["TEXT", _imap_quote(text.strip())]
    # IMAP SEARCH needs at least one key; "all" with no filter is the mailbox.
    return args or ["ALL"]


# ---------------------------------------------------------------------------
# IMAP plumbing (blocking; every caller runs these in a worker thread)
# ---------------------------------------------------------------------------

def _is_gmail(host: str) -> bool:
    return "gmail" in host or "google" in host


def _open(host: str, address: str, password: str, *, readonly: bool = True
          ) -> tuple[imaplib.IMAP4_SSL, int | None]:
    """Connect, log in and select INBOX. Returns ``(imap, inbox_total)``."""
    imap = imaplib.IMAP4_SSL(host, timeout=_IMAP_TIMEOUT_S)
    try:
        imap.login(address, password)
        typ, data = imap.select("INBOX", readonly=readonly)
        inbox_total: int | None = None
        if typ == "OK" and data and data[0] and data[0].strip().isdigit():
            inbox_total = int(data[0])
        return imap, inbox_total
    except Exception:
        _close(imap)
        raise


def _close(imap: imaplib.IMAP4_SSL) -> None:
    try:
        imap.logout()
    except Exception:  # noqa: BLE001
        pass


def _unseen_count(imap: imaplib.IMAP4_SSL) -> int | None:
    """How many unread messages INBOX holds in total (``STATUS``)."""
    try:
        typ, data = imap.status("INBOX", "(UNSEEN)")
        if typ == "OK" and data and data[0]:
            m = re.search(rb"UNSEEN\s+(\d+)", data[0])
            if m:
                return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def _search_uids(imap: imaplib.IMAP4_SSL, is_gmail: bool, category: str | None,
                 range_: str | None, query: str | None) -> list[bytes]:
    """UIDs matching the filters, oldest first (as the server lists them)."""
    ids: list[bytes] = []
    gmail_ok = False
    if is_gmail:
        gq = _gmail_query(category, range_, query)
        # The X-GM-RAW query must be sent as a single QUOTED string —
        # imaplib does not quote it, so a multi-word query like
        # "category:promotions newer_than:7d" would otherwise be split into
        # extra tokens and Gmail rejects it (the bogus "couldn't sign in").
        try:
            typ, data = imap.uid("SEARCH", "X-GM-RAW", _imap_quote(gq))
            if typ == "OK":
                gmail_ok = True
                if data and data[0]:
                    ids = data[0].split()
        except imaplib.IMAP4.error as exc:
            logger.warning("read_emails.xgmraw_failed", q=gq, error=str(exc))
    if not gmail_ok and not ids:
        # Fallback (non-Gmail, or X-GM-RAW errored).
        typ, data = imap.uid("SEARCH", *_imap_search_args(category, range_, query))
        if typ == "OK" and data and data[0]:
            ids = data[0].split()
    return ids


_UID_RE = re.compile(rb"\bUID\s+(\d+)")
_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)")
_LABELS_RE = re.compile(rb'X-GM-LABELS\s+\(((?:[^()"]|"[^"]*")*)\)')
_SIZE_RE = re.compile(rb"RFC822\.SIZE\s+(\d+)")


def _parse_meta(meta: bytes) -> tuple[str | None, set[str], bytes]:
    """``(uid, flags, gmail_labels_raw)`` from a FETCH response's metadata."""
    uid_m = _UID_RE.search(meta)
    uid = uid_m.group(1).decode() if uid_m else None
    flags_m = _FLAGS_RE.search(meta)
    flags = (
        {f.decode(errors="replace") for f in flags_m.group(1).split()}
        if flags_m else set()
    )
    labels_m = _LABELS_RE.search(meta)
    labels = labels_m.group(1) if labels_m else b""
    return uid, flags, labels


def _fetch_batch(imap: imaplib.IMAP4_SSL, uids: list[bytes], spec: str
                 ) -> list[tuple[str | None, bytes, bytes]]:
    """One round trip for many messages: ``[(uid, meta, raw), ...]``.

    A per-message FETCH costs a full round trip each — on a 200 ms link
    "what's in my inbox" for ten mails spent two seconds just waiting.
    """
    if not uids:
        return []
    typ, data = imap.uid("FETCH", b",".join(uids).decode(), spec)
    if typ != "OK" or not data:
        return []
    out: list[tuple[str | None, bytes, bytes]] = []
    for entry in data:
        if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], bytes):
            meta = entry[0] if isinstance(entry[0], bytes) else b""
            uid, _flags, _labels = _parse_meta(meta)
            out.append((uid, meta, entry[1]))
    return out


def _priority(msg: emaillib.message.Message) -> str:
    xp = (msg.get("X-Priority") or "").strip()
    imp = (msg.get("Importance") or msg.get("Priority") or "").strip().lower()
    if xp[:1] in ("1", "2") or imp in ("high", "urgent"):
        return "high"
    return "normal"


def _is_bulk(msg: emaillib.message.Message) -> bool:
    if any(msg.get(h) for h in ("List-Unsubscribe", "List-Id", "List-Post")):
        return True
    return (msg.get("Precedence") or "").strip().lower() in ("bulk", "list", "junk")


def _addresses(value: str | None) -> list[str]:
    if not value:
        return []
    return [addr for _name, addr in getaddresses([_decode(value)]) if addr]


def _build_item(msg: emaillib.message.Message, uid: str | None, flags: set[str],
                labels: bytes, *, full_body: bool, body_limit: int = _BODY_CHARS
                ) -> dict[str, Any]:
    """One message → the dict the model (and the client UI) sees.

    Threading headers are tucked under the private ``_thread`` key: the tool
    moves them into the session cache (:func:`remember_threads`) and they
    never reach the model, which only needs the ``uid``.
    """
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        when = dt.isoformat() if dt else None
    except Exception:  # noqa: BLE001
        when = None
    raw_from = _decode(msg.get("From"))
    # Split "Name <addr@x.com>" so the model has the EXACT address to
    # reply to — never guessing/hallucinating a recipient.
    from_name, from_email = parseaddr(raw_from)
    subject = _decode(msg.get("Subject"))
    message_id = (msg.get("Message-ID") or "").strip()
    in_reply_to = (msg.get("In-Reply-To") or "").strip()
    reply_to = _addresses(msg.get("Reply-To"))
    item: dict[str, Any] = {
        "uid": uid,
        "from": raw_from,
        "from_name": from_name or from_email,
        "from_email": from_email,
        "subject": subject,
        "date": when,
        "snippet": _snippet(msg),
        "unread": "\\Seen" not in flags,
    }
    # Signals for the importance score — popped before the model sees them.
    item["_signals"] = {
        "flagged": "\\Flagged" in flags,
        "gmail_important": bool(re.search(rb"\\+Important\b", labels)),
        "bulk": _is_bulk(msg),
        "priority": _priority(msg),
        "in_reply_to": in_reply_to,
        "to": _addresses(msg.get("To")),
        "cc": _addresses(msg.get("Cc")),
    }
    item["_thread"] = {
        "uid": uid,
        "message_id": message_id,
        "references": (msg.get("References") or "").strip(),
        "subject": subject,
        "from_email": from_email,
        "reply_to_email": reply_to[0] if reply_to else from_email,
    }
    if full_body:
        item["to"] = item["_signals"]["to"]
        item["cc"] = item["_signals"]["cc"]
        item["reply_to_email"] = item["_thread"]["reply_to_email"]
        item["body"] = _full_body(msg, body_limit)
        atts = _attachments(msg)
        if atts:
            item["attachments"] = atts
    return item


def _finish_items(items: list[dict[str, Any]], user_address: str) -> None:
    """Score importance from the private signals, then drop them.

    Only levels worth saying are attached (``normal`` is left out so the list
    view stays small — every key on every item is re-billed each turn).
    """
    for it in items:
        sig = it.pop("_signals", {}) or {}
        level, reasons, score = score_importance({**it, **sig}, user_address)
        it["_score"] = score
        if level != "normal":
            it["importance"] = level
        if level in ("critical", "high") and reasons:
            it["importance_reasons"] = reasons[:2]
        if sig.get("bulk"):
            it["_bulk"] = True


def _fetch_emails(
    host: str,
    address: str,
    password: str,
    limit: int,
    query: str | None,
    category: str | None,
    range_: str | None,
    full_body: bool = False,
    fetch_bytes: int | None = None,
    with_unread: bool = False,
) -> MailPage:
    """Blocking IMAP read of the matching messages (run in a thread).

    Returns a :class:`MailPage`: the newest ``limit`` matches (newest first)
    plus ``total`` (how many matched), ``inbox_total`` / ``inbox_unread``
    (the whole INBOX) and, with ``with_unread``, ``unread_total`` for the
    searched range.
    """
    is_gmail = _is_gmail(host)
    imap, inbox_total = _open(host, address, password)
    try:
        page = MailPage()
        page.inbox_total = inbox_total
        page.inbox_unread = _unseen_count(imap)
        ids = _search_uids(imap, is_gmail, category, range_, query)
        page.total = len(ids)
        if with_unread and category != "unread":
            if category:
                # The unread subset of a category is one more search.
                unread_ids = _search_uids(imap, is_gmail, "unread", range_, query)
                page.unread_total = len(set(unread_ids) & set(ids))
            else:
                page.unread_total = len(
                    _search_uids(imap, is_gmail, "unread", range_, query)
                )
        elif with_unread:
            page.unread_total = page.total
        if not ids:
            return page

        ids = ids[-limit:]
        # Full body only when asked (read_email); the list view reads a
        # bounded prefix — see _LIST_FETCH_BYTES. BODY.PEEK never sets \Seen
        # (belt and braces on top of readonly=True).
        prefix = fetch_bytes or _LIST_FETCH_BYTES
        body_spec = "BODY.PEEK[]" if full_body else f"BODY.PEEK[]<0.{prefix}>"
        labels_spec = " X-GM-LABELS" if is_gmail else ""
        spec = f"(FLAGS{labels_spec} {body_spec})"
        fetched = _fetch_batch(imap, ids, spec)
        by_uid = {uid: (meta, raw) for uid, meta, raw in fetched if uid}
        for mid in reversed(ids):  # newest first
            got = by_uid.get(mid.decode())
            if got is None:
                continue
            meta, raw = got
            uid, flags, labels = _parse_meta(meta)
            msg = emaillib.message_from_bytes(raw)
            page.append(_build_item(msg, uid, flags, labels, full_body=full_body))
        _finish_items(page, address)
        return page
    finally:
        _close(imap)


def fetch_thread_headers(host: str, address: str, password: str, uid: str
                         ) -> dict[str, Any] | None:
    """The headers a reply needs (Message-ID, References, Subject, From) for
    one message — the fallback when the session cache has no entry for it."""
    imap, _ = _open(host, address, password)
    try:
        typ, data = imap.uid(
            "FETCH", str(uid),
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES SUBJECT FROM REPLY-TO)])",
        )
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        msg = emaillib.message_from_bytes(data[0][1])
        _name, from_email = parseaddr(_decode(msg.get("From")))
        reply_to = _addresses(msg.get("Reply-To"))
        return {
            "uid": str(uid),
            "message_id": (msg.get("Message-ID") or "").strip(),
            "references": (msg.get("References") or "").strip(),
            "subject": _decode(msg.get("Subject")),
            "from_email": from_email,
            "reply_to_email": reply_to[0] if reply_to else from_email,
        }
    finally:
        _close(imap)


def fetch_raw_message(
    host: str, address: str, password: str, *, uid: str | None = None,
    query: str | None = None, range_: str | None = "month",
) -> dict[str, Any] | None:
    """The complete raw message (for forwarding), by ``uid`` or by search.

    Returns ``{uid, raw, size, truncated}`` or ``None`` when nothing matches.
    A message over :data:`_MAX_FORWARD_BYTES` comes back as its first 64 KB
    with ``truncated=True`` — the caller forwards the text without the
    attachments and says so.
    """
    imap, _ = _open(host, address, password)
    try:
        target: bytes | None = None
        if uid:
            target = str(uid).encode()
        else:
            ids = _search_uids(imap, _is_gmail(host), None, range_, query)
            if ids:
                target = ids[-1]
        if target is None:
            return None
        size: int | None = None
        typ, data = imap.uid("FETCH", target.decode(), "(RFC822.SIZE)")
        if typ == "OK" and data and data[0]:
            meta = data[0] if isinstance(data[0], bytes) else data[0][0]
            m = _SIZE_RE.search(meta or b"")
            if m:
                size = int(m.group(1))
        truncated = size is not None and size > _MAX_FORWARD_BYTES
        spec = (
            f"(BODY.PEEK[]<0.{_LIST_FETCH_BYTES}>)" if truncated else "(BODY.PEEK[])"
        )
        typ, data = imap.uid("FETCH", target.decode(), spec)
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return {
            "uid": target.decode(), "raw": data[0][1], "size": size,
            "truncated": truncated,
        }
    finally:
        _close(imap)


def set_seen(
    host: str, address: str, password: str, *, uid: str | None = None,
    query: str | None = None, category: str | None = None,
    range_: str | None = None, seen: bool = True, all_matching: bool = False,
) -> dict[str, Any]:
    """Set or clear ``\\Seen`` on one message (by ``uid`` or the newest match)
    or on every match (``all_matching``). Returns ``{count, uids, subject?,
    from?}``.
    """
    imap, _ = _open(host, address, password, readonly=False)
    try:
        if uid:
            uids = [str(uid).encode()]
        else:
            uids = _search_uids(imap, _is_gmail(host), category, range_ or "month", query)
            if not uids:
                return {"count": 0, "uids": []}
            uids = uids[-_MAX_STORE:] if all_matching else uids[-1:]
        op = "+FLAGS.SILENT" if seen else "-FLAGS.SILENT"
        typ, _data = imap.uid("STORE", b",".join(uids).decode(), op, r"(\Seen)")
        if typ != "OK":
            raise imaplib.IMAP4.error("STORE failed")
        out: dict[str, Any] = {
            "count": len(uids), "uids": [u.decode() for u in uids],
        }
        if len(uids) == 1:
            typ, data = imap.uid(
                "FETCH", uids[0].decode(),
                "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])",
            )
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                # A uid that names nothing: the STORE was a no-op.
                return {"count": 0, "uids": []}
            msg = emaillib.message_from_bytes(data[0][1])
            out["subject"] = _decode(msg.get("Subject"))
            out["from"] = _decode(msg.get("From"))
        return out
    finally:
        _close(imap)


async def _fetch_with_retry(
    host: str,
    address: str,
    password: str,
    limit: int,
    query: str | None,
    category: str | None,
    range_: str | None,
    full_body: bool = False,
    **extra: Any,
) -> MailPage:
    """One automatic retry on NETWORK failure (never on auth).

    The user's phone-to-server leg is already retried by the app; this covers
    the server-to-mailbox leg, where a single dropped TLS handshake otherwise
    turns into "couldn't read email" for a mailbox that is perfectly fine.
    Auth errors are excluded — retrying a wrong password just doubles the
    delay before the honest answer.

    ``extra`` (``fetch_bytes`` / ``with_unread``) is only passed through when
    set, so a caller (or a test double) that only knows the classic
    signature keeps working.
    """
    kwargs = {k: v for k, v in extra.items() if v}
    try:
        return await asyncio.to_thread(
            _fetch_emails, host, address, password, limit, query,
            category, range_, full_body, **kwargs,
        )
    except (OSError, TimeoutError) as exc:
        logger.info("read_emails.retrying", host=host, error=str(exc))
        await asyncio.sleep(0.5)
        return await asyncio.to_thread(
            _fetch_emails, host, address, password, limit, query,
            category, range_, full_body, **kwargs,
        )


# ---------------------------------------------------------------------------
# Session cache of threading headers
# ---------------------------------------------------------------------------

def thread_key(address: str, uid: str | None) -> str:
    """Cache key for a message: UIDs are only unique per mailbox."""
    return f"{address.lower()}:{uid}"


def remember_threads(ctx: ToolContext, items: list[dict[str, Any]], address: str
                     ) -> None:
    """Move each item's ``_thread`` headers into the session cache.

    Keyed by ``address:uid`` and by Message-ID, so ``send_email`` can thread a
    reply from just the ``uid`` the model read out — the model never has to
    carry a Message-ID or a References chain through the conversation.
    """
    cache = ctx.email_threads
    for it in items:
        th = it.pop("_thread", None)
        if cache is None or not th:
            continue
        cache[thread_key(address, th.get("uid"))] = th
        if th.get("message_id"):
            cache[th["message_id"]] = th
    if cache is not None:
        while len(cache) > _THREAD_CACHE_MAX:
            cache.pop(next(iter(cache)))


def public_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip private (``_``-prefixed) working keys before a result goes out."""
    return [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]


def _more_instruction(count: int, total: int) -> str:
    return (
        f"{total} emails match but only the newest {count} are listed here. "
        f"Say 'more than {count}' / '{count}+' or the exact number {total} — "
        f"never tell the user they received just {count}."
    )


def _auth_message(label: str) -> dict[str, Any]:
    return {
        "ok": False,
        "message": (
            f"Couldn't sign in to {label}. Check the address and app "
            "password in Settings."
        ),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class ReadEmailsTool(Tool):
    """Read the user's emails over IMAP with optional category/date filters."""

    name = "read_emails"
    description = (
        "Read the user's emails (sender, subject, snippet, uid, unread, and an "
        "importance tag with reasons). Filter by category (promotions, social, "
        "updates, important, unread, starred, primary) and/or a time range "
        "(today, yesterday, week, month, all). To FIND someone's emails pass "
        "`query` = their name or address (on Gmail the Gmail search syntax "
        "works too: from:ali, subject:invoice, has:attachment); a query "
        "searches the last month unless a range is given. The result's `total` "
        "is how many emails MATCHED — when `has_more` is true, more arrived "
        "than are listed, so say 'more than N'. Use for any question about "
        "their inbox / mail."
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
                "enum": _RANGES,
                "description": "Time window: default today, or the last month "
                "when a query is given; 'all' = the whole mailbox.",
            },
            "query": {
                "type": "string",
                "description": "Sender name / address or a keyword to search "
                "for (e.g. 'Faraz', 'faraz@gmail.com', 'invoice', or on Gmail "
                "'from:faraz has:attachment').",
            },
            "limit": {
                "type": "integer",
                "description": "How many emails to read (default 10).",
            },
            "account": {
                "type": "string",
                "description": "Which mailbox to read, as the user said it: "
                "'primary', 'secondary', the account label, or the address. "
                "Omit it on the FIRST email request of a session: the tool "
                "then tells you which accounts exist and what to ask the user "
                "(never assume one). Pass 'all' only when the user explicitly "
                "asks for every mailbox.",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        account_arg = (kwargs.get("account") or "").strip()
        try:
            limit = max(1, min(int(kwargs.get("limit") or 10), _MAX_LIMIT))
        except (TypeError, ValueError):
            limit = 10
        category = (kwargs.get("category") or None)
        range_ = (kwargs.get("range") or None)
        query = (kwargs.get("query") or None)

        # "all" → read from every mailbox and merge newest-first.
        if account_arg.lower() == "all":
            accts = usable_accounts(ctx)
            if not accts:
                _none, result = resolve_account(ctx, None)
                return result or {"ok": False, "message": NO_ACCOUNT_MESSAGE}
            merged: list[dict[str, Any]] = []
            failed: list[str] = []
            total = 0
            inbox_unread = 0
            for acct in accts:
                host, address, password, label = _imap_creds(acct)
                try:
                    items = await _fetch_with_retry(
                        host, address, password, limit, query,
                        category, range_,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "read_emails.account_failed", account=label,
                        error=str(exc),
                    )
                    failed.append(label)
                    continue
                remember_threads(ctx, items, address)
                total += getattr(items, "total", len(items))
                inbox_unread += getattr(items, "inbox_unread", None) or 0
                for it in items:
                    it["account"] = label
                merged.extend(items)
            # Every mailbox failing is an OUTAGE, not an empty inbox — saying
            # "no new mail" here would be a lie the user acts on.
            if not merged and failed and len(failed) == len(accts):
                return {
                    "ok": False,
                    "message": (
                        "Couldn't reach any mailbox right now "
                        f"({', '.join(failed)}). Tell the user checking email "
                        "failed — do NOT say the inbox is empty."
                    ),
                }
            merged.sort(key=lambda e: e.get("date") or "", reverse=True)
            merged = public_items(merged[:limit])
            result: dict[str, Any] = {
                "ok": True, "count": len(merged), "total": total,
                "has_more": total > len(merged), "category": category,
                "range": range_ or "today", "account": "all",
                "inbox_unread": inbox_unread,
            }
            notes: list[str] = []
            if total > len(merged):
                notes.append(_more_instruction(len(merged), total))
            if failed:
                result["unreachable_accounts"] = failed
                notes.append(
                    f"NOTE: the {', '.join(failed)} mailbox could not be "
                    "read — mention that alongside the results."
                )
            if notes:
                result["_instruction"] = " ".join(notes)
            result["emails"] = merged
            return result

        # One mailbox: the one the user named or already settled on. Until
        # they have, the answer is the question to ask them — never a guess.
        account, ask = resolve_account(ctx, account_arg or None)
        if ask:
            return ask
        host, address, password, label = _imap_creds(account)
        try:
            emails = await _fetch_with_retry(
                host, address, password, limit, query, category, range_,
            )
        except imaplib.IMAP4.error as exc:
            logger.warning("read_emails.auth_failed", error=str(exc))
            return _auth_message(label)
        except UnicodeEncodeError:
            return {
                "ok": False,
                "message": "I can only search mail by English-letter keywords.",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("read_emails.failed", error=str(exc))
            return {"ok": False, "message": "Couldn't read email right now."}
        remember_threads(ctx, emails, address)
        total = getattr(emails, "total", len(emails))
        items = public_items(list(emails))
        # Counts and guidance first, the list last: a result the orchestrator
        # clips for the model loses its tail, and that must never be the
        # "more than ten" instruction.
        result = {
            "ok": True,
            "count": len(items),
            "total": total,
            "has_more": total > len(items),
            "category": category,
            "range": range_ or "today",
            "account": label,
        }
        inbox_total = getattr(emails, "inbox_total", None)
        inbox_unread = getattr(emails, "inbox_unread", None)
        if inbox_total is not None:
            result["inbox_total"] = inbox_total
        if inbox_unread is not None:
            result["inbox_unread"] = inbox_unread
        if total > len(items):
            result["_instruction"] = _more_instruction(len(items), total)
        result["emails"] = items
        return result


class ReadEmailTool(Tool):
    """Read ONE full email — the complete message body."""

    name = "read_email"
    description = (
        "Read the FULL body of a single email, found by its uid (from "
        "read_emails / inbox_summary) or by a sender / subject keyword. Use "
        "when the user wants to hear the whole email, its key points or "
        "takeaways, or a reply drafted. The result includes `reply_hint` "
        "with exactly what to pass to send_email for a threaded reply."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "uid": {
                "type": "string",
                "description": "The email's uid from an earlier read_emails / "
                "inbox_summary result — the surest way to pick the right one.",
            },
            "query": {
                "type": "string",
                "description": "Sender name/address or subject keyword to "
                "identify the email (e.g. 'the one from GitHub').",
            },
            "range": {
                "type": "string",
                "enum": _RANGES,
                "description": "Time window to search (default the last "
                "month; 'all' = the whole mailbox).",
            },
            "account": {
                "type": "string",
                "description": "Which mailbox to search, as the user said it: "
                "'primary', 'secondary', the account label, or the address. "
                "Omit it on the first email request of a session: the tool "
                "then tells you what to ask the user (never assume one).",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        account, ask = resolve_account(ctx, (kwargs.get("account") or "").strip() or None)
        if ask:
            return ask
        host, address, password, label = _imap_creds(account)
        uid = str(kwargs.get("uid") or "").strip() or None
        query = (kwargs.get("query") or None)
        range_ = (kwargs.get("range") or "month")
        try:
            if uid:
                emails = await asyncio.to_thread(
                    _fetch_one_by_uid, host, address, password, uid,
                )
            else:
                emails = await _fetch_with_retry(
                    host, address, password, 1, query, None, range_, True,
                )
        except imaplib.IMAP4.error as exc:
            logger.warning("read_email.auth_failed", error=str(exc))
            return _auth_message(label)
        except Exception as exc:  # noqa: BLE001
            logger.warning("read_email.failed", error=str(exc))
            return {"ok": False, "message": "Couldn't read the email."}
        if not emails:
            return {"ok": False, "message": "No matching email found."}
        remember_threads(ctx, emails, address)
        item = public_items(list(emails))[0]
        reply_to = item.get("reply_to_email") or item.get("from_email")
        subject = item.get("subject") or ""
        item["reply_hint"] = {
            "to": reply_to,
            "subject": subject if re.match(r"^\s*re\s*:", subject, re.I)
            else f"Re: {subject}".strip(),
            "reply_to_uid": item.get("uid"),
        }
        item["_instruction"] = (
            "Give the headline first (who, what they want, any date or amount), "
            "then offer the detail. For takeaways or a summary: at most three or "
            "four short spoken sentences, no numbering. If the user wants to "
            "reply, draft one that matches the sender's tone and the user's "
            "intent, read it back, and only on their yes call send_email with "
            "reply_hint's to, subject and reply_to_uid."
        )
        return {"ok": True, "account": label, **item}


def _fetch_one_by_uid(host: str, address: str, password: str, uid: str
                      ) -> MailPage:
    """Full read of exactly one message by UID (``read_email(uid=…)``)."""
    is_gmail = _is_gmail(host)
    imap, inbox_total = _open(host, address, password)
    try:
        page = MailPage()
        page.inbox_total = inbox_total
        labels_spec = " X-GM-LABELS" if is_gmail else ""
        fetched = _fetch_batch(imap, [str(uid).encode()], f"(FLAGS{labels_spec} BODY.PEEK[])")
        for got_uid, meta, raw in fetched:
            _u, flags, labels = _parse_meta(meta)
            msg = emaillib.message_from_bytes(raw)
            page.append(_build_item(msg, got_uid or str(uid), flags, labels, full_body=True))
        page.total = len(page)
        _finish_items(page, address)
        return page
    finally:
        _close(imap)
