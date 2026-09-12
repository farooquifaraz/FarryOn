"""``send_email`` and ``forward_email`` tools: mail FROM the user's account
over SMTP.

Uses the same address + app password the user configured for reading. SMTP
runs in a worker thread so the realtime event loop is never blocked.

SAFETY: sending is an outward action. The model is instructed (system prompt)
to read the draft back and get an explicit spoken confirmation BEFORE calling
either tool — never auto-send.

Replies thread properly: ``send_email(reply_to_uid=…)`` looks up the original
message's ``Message-ID`` / ``References`` (from the session cache the read
tools fill, else one IMAP header fetch) and sets ``In-Reply-To`` /
``References`` so Gmail, Outlook and the rest show the reply inside the
conversation instead of as a stray new mail.
"""

from __future__ import annotations

import asyncio
import email as emaillib
import hashlib
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import (
    formatdate,
    getaddresses,
    make_msgid,
    parsedate_to_datetime,
)
from typing import Any

from app.logging_conf import get_logger
from app.tools import email_read
from app.tools.base import Tool, ToolContext
from app.tools.email_accounts import resolve_account
from app.tools.idempotency import already_sent, mark_sent  # UX Spec §3.4
from app.tools.validators import valid_email  # UX Spec §3.1

logger = get_logger(__name__)

_DEFAULT_SMTP_HOST = "smtp.gmail.com"
_DEFAULT_SMTP_PORT = 587

#: A model-composed body has no natural ceiling; a runaway one must not
#: become a megabyte email sent in the user's name.
_MAX_BODY = 50_000

#: Attachments re-attached on a forward are capped in total; the rest are
#: named in the result so the assistant can say what was left out.
_MAX_FORWARD_ATTACH_BYTES = 20 * 1024 * 1024

_RE_PREFIX = re.compile(r"^\s*(re|aw|sv|antw)\s*:", re.I)
_FWD_PREFIX = re.compile(r"^\s*(fwd?|wg|tr)\s*:", re.I)


def _smtp_creds(account: dict[str, Any]) -> tuple[str, int, str, str, str]:
    """``(host, port, address, password, label)`` for one mailbox dict."""
    address = (account.get("address") or "").strip()
    password = (account.get("appPassword") or "").strip()
    host = (account.get("smtpHost") or _DEFAULT_SMTP_HOST).strip() \
        or _DEFAULT_SMTP_HOST
    try:
        port = int(account.get("smtpPort") or _DEFAULT_SMTP_PORT)
    except (TypeError, ValueError):
        port = _DEFAULT_SMTP_PORT
    return host, port, address, password, (account.get("label") or address)


def _deliver(host: str, port: int, address: str, password: str,
             msg: EmailMessage) -> None:
    """Blocking SMTP delivery of a built message (run in a thread).

    Port 465 uses implicit TLS (SMTP_SSL); any other port (587, 25) uses
    STARTTLS — covering Gmail, Outlook/365, Yahoo, Hostinger and custom servers.
    ``send_message`` reads To/Cc/Bcc from the headers and strips Bcc before
    the message goes on the wire.
    """
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as s:
            s.login(address, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls(context=context)
            s.login(address, password)
            s.send_message(msg)


def _send(host: str, port: int, address: str, password: str, to: str,
          subject: str, body: str, cc: list[str] | None = None,
          bcc: list[str] | None = None,
          headers: dict[str, str] | None = None) -> None:
    """Blocking SMTP send of a plain-text mail (run in a thread)."""
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=address.rsplit("@", 1)[-1] or None)
    for key, value in (headers or {}).items():
        if value:
            msg[key] = value
    msg.set_content(body)
    _deliver(host, port, address, password, msg)


def _split_addresses(value: Any) -> list[str]:
    """``"a@x.com, Ali <b@y.com>; a@x.com"`` (or a list) → ``["a@x.com",
    "b@y.com"]`` — display names dropped, duplicates collapsed."""
    if isinstance(value, list):
        text = ", ".join(str(v) for v in value)
    else:
        text = str(value or "")
    text = text.replace(";", ",").replace("\n", ",")
    out: list[str] = []
    for _name, addr in getaddresses([text]):
        addr = addr.strip()
        if addr and addr.lower() not in {o.lower() for o in out}:
            out.append(addr)
    return out


def _validate_addresses(kind: str, values: list[str]
                        ) -> tuple[list[str], dict[str, Any] | None]:
    """Every address must be complete, or the whole send is refused."""
    clean: list[str] = []
    for v in values:
        ok, addr = valid_email(v)
        if not ok:
            return [], {
                "ok": False,
                "message": (
                    f"The {kind} address '{v}' doesn't look like a complete "
                    "email address. Read it back to the user and confirm it."
                ),
            }
        clean.append(addr)
    return clean, None


async def _thread_headers(
    ctx: ToolContext, account: dict[str, Any], address: str,
    reply_to_uid: str | None, in_reply_to: str | None,
) -> dict[str, Any] | None:
    """The original message's threading headers, from the session cache or
    (for a uid the cache never saw) one IMAP header fetch."""
    cache = ctx.email_threads or {}
    found: dict[str, Any] | None = None
    if reply_to_uid:
        found = cache.get(email_read.thread_key(address, reply_to_uid))
    if found is None and in_reply_to:
        found = cache.get(in_reply_to.strip())
    if found is None and reply_to_uid:
        host, imap_address, password, _label = email_read._imap_creds(account)
        try:
            found = await asyncio.to_thread(
                email_read.fetch_thread_headers, host, imap_address, password,
                reply_to_uid,
            )
        except Exception as exc:  # noqa: BLE001 — threading is best-effort
            logger.warning("send_email.thread_lookup_failed", error=str(exc))
            found = None
    if found is None and in_reply_to:
        # Nothing cached, but the model gave us a Message-ID: honour it.
        found = {"message_id": in_reply_to.strip(), "references": "", "subject": ""}
    return found


def _reply_headers(thread: dict[str, Any]) -> dict[str, str]:
    """``In-Reply-To`` / ``References`` for a reply to ``thread``."""
    message_id = (thread.get("message_id") or "").strip()
    if not message_id:
        return {}
    refs = (thread.get("references") or "").strip()
    references = f"{refs} {message_id}".strip() if refs else message_id
    return {"In-Reply-To": message_id, "References": references}


def _reply_subject(subject: str, thread: dict[str, Any] | None) -> str:
    """``Re: <original>`` unless the caller already gave a threaded subject."""
    if thread and thread.get("subject"):
        original = thread["subject"].strip()
        if not subject or subject.lower() == "(no subject)":
            return original if _RE_PREFIX.match(original) else f"Re: {original}"
        if _RE_PREFIX.match(subject):
            return subject
        if subject.strip().lower() == original.lower():
            return f"Re: {original}"
        return subject
    # No original to thread on: the caller's subject stands as given.
    return subject


def _smtp_failure(exc: Exception, to: str) -> dict[str, Any]:
    """A friendly, honest tool result for a failed delivery."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        logger.warning("send_email.auth_failed", error=str(exc))
        return {
            "ok": False,
            "message": (
                "Couldn't sign in to send. Check the address and app "
                "password in Settings."
            ),
        }
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        logger.warning("send_email.recipient_refused", error=str(exc))
        return {
            "ok": False,
            "message": (
                f"The mail server refused the address {to} — it may not "
                "exist. Read the address back to the user and confirm it."
            ),
        }
    if isinstance(exc, (OSError, TimeoutError)):
        logger.warning("send_email.network_failed", error=str(exc))
        return {
            "ok": False,
            "message": (
                "Couldn't reach the mail server just now (network). The "
                "email was NOT sent — offer to try again."
            ),
        }
    logger.warning("send_email.failed", error=str(exc))
    return {"ok": False, "message": "Couldn't send the email right now."}


class SendEmailTool(Tool):
    """Send an email from the user's configured account."""

    name = "send_email"
    description = (
        "Send an email from the user's account. IMPORTANT: only call this "
        "AFTER reading the recipient, subject and body back to the user and "
        "getting their explicit confirmation — never send without a clear yes. "
        "To REPLY to an email the user heard, pass its `reply_to_uid` (the "
        "uid from read_emails / read_email) so the reply lands in the same "
        "conversation; the tool sets the Re: subject and threading headers. "
        "Optional `cc` / `bcc` take one or more addresses separated by commas. "
        "The sending account is never assumed either: omit 'account' the first "
        "time and the tool tells you which accounts exist and what to ask; "
        "then pass what the user said ('primary', 'secondary', a label or an "
        "address)."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address (several: comma-separated).",
            },
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "cc": {
                "type": "string",
                "description": "CC address(es), comma-separated. Only when the "
                "user asked to copy someone.",
            },
            "bcc": {
                "type": "string",
                "description": "BCC address(es), comma-separated. Only when the "
                "user asked for a blind copy.",
            },
            "reply_to_uid": {
                "type": "string",
                "description": "When replying: the uid of the email being "
                "answered (from read_emails / read_email / inbox_summary). "
                "Threads the reply and prefixes 'Re:' automatically.",
            },
            "in_reply_to": {
                "type": "string",
                "description": "Alternative to reply_to_uid: the Message-ID "
                "of the email being answered, if that is what you have.",
            },
            "account": {
                "type": "string",
                "description": "Which mailbox to send FROM, as the user said "
                "it: 'primary', 'secondary', the account label, or the "
                "address. Omit on the first email request of a session and "
                "the tool tells you what to ask (never assume one).",
            },
        },
        "required": ["to", "body"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        account_arg = (kwargs.get("account") or "").strip()
        # Send-safety: the sender is the mailbox the user confirmed or chose
        # this session — never a guess. Until they have, the answer is the
        # question to ask them.
        account, ask = resolve_account(ctx, account_arg or None)
        if ask:
            return ask
        host, port, address, password, label = _smtp_creds(account)

        # CHANGED (UX Spec §3.1): real email validation instead of `"@" in to`,
        # which accepted "@", "a@" and "a@b" (no TLD).
        to_list = _split_addresses(kwargs.get("to"))
        if not to_list:
            return {
                "ok": False,
                "message": "That doesn't look like a complete email address.",
            }
        to_list, bad = _validate_addresses("recipient", to_list)
        if bad:
            return bad
        cc_list, bad = _validate_addresses("CC", _split_addresses(kwargs.get("cc")))
        if bad:
            return bad
        bcc_list, bad = _validate_addresses("BCC", _split_addresses(kwargs.get("bcc")))
        if bad:
            return bad
        to = ", ".join(to_list)

        subject = (kwargs.get("subject") or "").strip()[:500]
        body = (kwargs.get("body") or "")[:_MAX_BODY]

        reply_to_uid = str(kwargs.get("reply_to_uid") or "").strip() or None
        in_reply_to = (kwargs.get("in_reply_to") or "").strip() or None
        thread: dict[str, Any] | None = None
        headers: dict[str, str] = {}
        if reply_to_uid or in_reply_to:
            thread = await _thread_headers(
                ctx, account, address, reply_to_uid, in_reply_to,
            )
            if thread:
                headers = _reply_headers(thread)
            subject = _reply_subject(subject, thread)
        subject = subject or "(no subject)"

        # CHANGED (UX Spec §3.4): idempotency. Email is a REAL outward send, so a
        # retried turn (model re-issuing the send, or a reconnect replay) could
        # deliver the same mail twice. A fingerprint of sender+recipient+content
        # suppresses an identical resend inside a short window.
        fingerprint = (
            f"email:{address}->{to}|{','.join(cc_list)}|{','.join(bcc_list)}:"
            + hashlib.sha1(
                f"{subject}\n{body}".encode("utf-8")
            ).hexdigest()
        )
        result: dict[str, Any] = {
            "ok": True, "to": to, "subject": subject,
            "from": address, "account": label, "sent": True,
        }
        if cc_list:
            result["cc"] = cc_list
        if bcc_list:
            result["bcc"] = bcc_list
        if headers:
            result["threaded"] = True
        elif reply_to_uid or in_reply_to:
            # Sent as a fresh mail with a Re: subject — still a reply for
            # the human, just not linked for the mail client.
            result["threaded"] = False
        if already_sent(fingerprint):
            logger.info("send_email.deduped", to=to)
            return {**result, "deduped": True}

        try:
            await asyncio.to_thread(
                _send, host, port, address, password, to, subject, body,
                cc_list or None, bcc_list or None, headers or None,
            )
        except Exception as exc:  # noqa: BLE001
            return _smtp_failure(exc, to)
        mark_sent(fingerprint)  # UX Spec §3.4: block an identical resend
        logger.info("send_email.sent", to=to, account=label, threaded=bool(headers))
        return result


# ---------------------------------------------------------------------------
# forward_email
# ---------------------------------------------------------------------------

def _quoted_original(msg: emaillib.message.Message, text: str) -> str:
    """The forwarded block the way mail clients write it."""
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        when = dt.strftime("%a, %d %b %Y %H:%M") if dt else (msg.get("Date") or "")
    except Exception:  # noqa: BLE001
        when = msg.get("Date") or ""
    lines = [
        "---------- Forwarded message ----------",
        f"From: {email_read._decode(msg.get('From'))}",
        f"Date: {when}",
        f"Subject: {email_read._decode(msg.get('Subject'))}",
        f"To: {email_read._decode(msg.get('To'))}",
    ]
    cc = email_read._decode(msg.get("Cc"))
    if cc:
        lines.append(f"Cc: {cc}")
    return "\n".join(lines) + "\n\n" + text


def _build_forward(
    raw: bytes, *, sender: str, to: list[str], cc: list[str], bcc: list[str],
    note: str, with_attachments: bool,
) -> tuple[EmailMessage, dict[str, Any]]:
    """The outgoing forward + what it carries (``subject``, ``attachments``,
    ``attachments_skipped``)."""
    original = emaillib.message_from_bytes(raw)
    orig_subject = email_read._decode(original.get("Subject")) or "(no subject)"
    subject = orig_subject if _FWD_PREFIX.match(orig_subject) else f"Fwd: {orig_subject}"
    text = email_read._full_body(original, _MAX_BODY)

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] or None)
    body = (note.strip() + "\n\n" if note.strip() else "") + _quoted_original(original, text)
    msg.set_content(body[:_MAX_BODY + 2000])

    attached: list[str] = []
    skipped: list[str] = []
    if with_attachments and original.is_multipart():
        budget = _MAX_FORWARD_ATTACH_BYTES
        for part in original.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_disposition() != "attachment" and not part.get_filename():
                continue
            name = email_read._decode(part.get_filename()) or "attachment"
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:  # noqa: BLE001
                skipped.append(name)
                continue
            if len(payload) > budget:
                skipped.append(name)
                continue
            budget -= len(payload)
            maintype, _, subtype = part.get_content_type().partition("/")
            msg.add_attachment(
                payload, maintype=maintype or "application",
                subtype=subtype or "octet-stream", filename=name,
            )
            attached.append(name)
    return msg, {
        "subject": subject, "original_subject": orig_subject,
        "original_from": email_read._decode(original.get("From")),
        "attachments": attached, "attachments_skipped": skipped,
    }


class ForwardEmailTool(Tool):
    """Forward an email the user read to someone else."""

    name = "forward_email"
    description = (
        "Forward an existing email (with its attachments) to someone. Pick "
        "the email by its uid (from read_emails / read_email / inbox_summary) "
        "or by a sender / subject keyword; `note` is the user's own message "
        "on top. IMPORTANT: only call this AFTER reading back who it goes to "
        "and which email it is, and getting the user's explicit yes."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address (several: comma-separated).",
            },
            "uid": {
                "type": "string",
                "description": "The uid of the email to forward (preferred).",
            },
            "query": {
                "type": "string",
                "description": "If no uid: sender or subject keyword to find the "
                "email (the newest match is forwarded).",
            },
            "range": {
                "type": "string",
                "enum": email_read._RANGES,
                "description": "Search window when using query (default the "
                "last month; 'all' = the whole mailbox).",
            },
            "note": {
                "type": "string",
                "description": "What the user wants to say above the forwarded "
                "email, if anything.",
            },
            "cc": {"type": "string", "description": "CC address(es), comma-separated."},
            "bcc": {"type": "string", "description": "BCC address(es), comma-separated."},
            "account": {
                "type": "string",
                "description": "Which mailbox, as the user said it: 'primary', "
                "'secondary', a label or an address. Omit on the first email "
                "request of a session and the tool tells you what to ask.",
            },
        },
        "required": ["to"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        account, ask = resolve_account(ctx, (kwargs.get("account") or "").strip() or None)
        if ask:
            return ask
        host, port, address, password, label = _smtp_creds(account)
        imap_host, imap_address, imap_password, _l = email_read._imap_creds(account)

        to_list, bad = _validate_addresses("recipient", _split_addresses(kwargs.get("to")))
        if bad:
            return bad
        if not to_list:
            return {"ok": False, "message": "That doesn't look like a complete email address."}
        cc_list, bad = _validate_addresses("CC", _split_addresses(kwargs.get("cc")))
        if bad:
            return bad
        bcc_list, bad = _validate_addresses("BCC", _split_addresses(kwargs.get("bcc")))
        if bad:
            return bad
        uid = str(kwargs.get("uid") or "").strip() or None
        query = (kwargs.get("query") or "").strip() or None
        if not uid and not query:
            return {
                "ok": False,
                "message": "Which email should I forward? Give me the sender or subject.",
            }
        note = (kwargs.get("note") or "")[:5000]

        try:
            found = await asyncio.to_thread(
                email_read.fetch_raw_message, imap_host, imap_address,
                imap_password, uid=uid, query=query,
                range_=kwargs.get("range") or "month",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("forward_email.fetch_failed", error=str(exc))
            return {"ok": False, "message": f"Couldn't read that email from {label}."}
        if not found:
            return {"ok": False, "message": "No matching email found to forward."}

        msg, info = _build_forward(
            found["raw"], sender=address, to=to_list, cc=cc_list, bcc=bcc_list,
            note=note, with_attachments=not found.get("truncated"),
        )
        to = ", ".join(to_list)
        fingerprint = f"fwd:{address}->{to}:{found['uid']}:" + hashlib.sha1(
            note.encode("utf-8")
        ).hexdigest()
        result: dict[str, Any] = {
            "ok": True, "sent": True, "to": to, "from": address,
            "account": label, "uid": found["uid"], **info,
        }
        if cc_list:
            result["cc"] = cc_list
        if bcc_list:
            result["bcc"] = bcc_list
        if found.get("truncated"):
            result["attachments_skipped"] = ["(all — the email is too large)"]
            result["_instruction"] = (
                "The original was too large to carry its attachments; only the "
                "text was forwarded — tell the user."
            )
        elif info["attachments_skipped"]:
            result["_instruction"] = (
                "Some attachments were too large and were left out: "
                + ", ".join(info["attachments_skipped"]) + ". Tell the user."
            )
        if already_sent(fingerprint):
            logger.info("forward_email.deduped", to=to)
            return {**result, "deduped": True}
        try:
            await asyncio.to_thread(_deliver, host, port, address, password, msg)
        except Exception as exc:  # noqa: BLE001
            return _smtp_failure(exc, to)
        mark_sent(fingerprint)
        logger.info(
            "forward_email.sent", to=to, account=label,
            attachments=len(info["attachments"]),
        )
        return result
