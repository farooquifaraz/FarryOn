"""``send_email`` tool: send mail FROM the user's account over SMTP.

Uses the same address + app password the user configured for reading. SMTP runs
in a worker thread so the realtime event loop is never blocked.

SAFETY: sending is an outward action. The model is instructed (system prompt)
to read the draft back and get an explicit spoken confirmation BEFORE calling
this tool — never auto-send.
"""

from __future__ import annotations

import asyncio
import hashlib
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext
from app.tools.idempotency import already_sent, mark_sent  # UX Spec §3.4
from app.tools.validators import valid_email  # UX Spec §3.1

logger = get_logger(__name__)

_DEFAULT_SMTP_HOST = "smtp.gmail.com"
_DEFAULT_SMTP_PORT = 587


def _send(host: str, port: int, address: str, password: str, to: str,
          subject: str, body: str) -> None:
    """Blocking SMTP send (run in a thread).

    Port 465 uses implicit TLS (SMTP_SSL); any other port (587, 25) uses
    STARTTLS — covering Gmail, Outlook/365, Yahoo, Hostinger and custom servers.
    """
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
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


class SendEmailTool(Tool):
    """Send an email from the user's configured account."""

    name = "send_email"
    description = (
        "Send an email from the user's account. IMPORTANT: only call this "
        "AFTER reading the recipient, subject and body back to the user and "
        "getting their explicit confirmation — never send without a clear yes."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address.",
            },
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "body"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        cfg = ctx.email or {}
        address = (cfg.get("address") or "").strip()
        password = (cfg.get("appPassword") or "").strip()
        host = (cfg.get("smtpHost") or _DEFAULT_SMTP_HOST).strip() \
            or _DEFAULT_SMTP_HOST
        try:
            port = int(cfg.get("smtpPort") or _DEFAULT_SMTP_PORT)
        except (TypeError, ValueError):
            port = _DEFAULT_SMTP_PORT
        if not address or not password:
            return {
                "ok": False,
                "message": (
                    "No email is configured. Ask the user to add their email "
                    "address and app password in Settings."
                ),
            }
        to = (kwargs.get("to") or "").strip()
        # CHANGED (UX Spec §3.1): real email validation instead of `"@" in to`,
        # which accepted "@", "a@" and "a@b" (no TLD).
        ok_addr, to = valid_email(to)
        if not ok_addr:
            return {
                "ok": False,
                "message": "That doesn't look like a complete email address.",
            }
        subject = (kwargs.get("subject") or "(no subject)").strip()
        body = kwargs.get("body") or ""

        # CHANGED (UX Spec §3.4): idempotency. Email is a REAL outward send, so a
        # retried turn (model re-issuing the send, or a reconnect replay) could
        # deliver the same mail twice. A fingerprint of sender+recipient+content
        # suppresses an identical resend inside a short window.
        fingerprint = (
            f"email:{address}->{to}:"
            + hashlib.sha1(
                f"{subject}\n{body}".encode("utf-8")
            ).hexdigest()
        )
        if already_sent(fingerprint):
            logger.info("send_email.deduped", to=to)
            return {
                "ok": True, "to": to, "subject": subject,
                "sent": True, "deduped": True,
            }

        try:
            await asyncio.to_thread(
                _send, host, port, address, password, to, subject, body
            )
        except smtplib.SMTPAuthenticationError as exc:
            logger.warning("send_email.auth_failed", error=str(exc))
            return {
                "ok": False,
                "message": (
                    "Couldn't sign in to send. Check the address and app "
                    "password in Settings."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("send_email.failed", error=str(exc))
            return {"ok": False, "message": "Couldn't send the email right now."}
        mark_sent(fingerprint)  # UX Spec §3.4: block an identical resend
        logger.info("send_email.sent", to=to)
        return {"ok": True, "to": to, "subject": subject, "sent": True}
