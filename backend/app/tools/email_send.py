"""``send_email`` tool: send mail FROM the user's account over SMTP.

Uses the same address + app password the user configured for reading. SMTP runs
in a worker thread so the realtime event loop is never blocked.

SAFETY: sending is an outward action. The model is instructed (system prompt)
to read the draft back and get an explicit spoken confirmation BEFORE calling
this tool — never auto-send.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext

logger = get_logger(__name__)

_DEFAULT_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def _send(host: str, address: str, password: str, to: str, subject: str,
          body: str) -> None:
    """Blocking SMTP send (run in a thread)."""
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP(host, _SMTP_PORT, timeout=20) as server:
        server.starttls(context=context)
        server.login(address, password)
        server.send_message(msg)


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
        if not address or not password:
            return {
                "ok": False,
                "message": (
                    "No email is configured. Ask the user to add their email "
                    "address and app password in Settings."
                ),
            }
        to = (kwargs.get("to") or "").strip()
        if not to or "@" not in to:
            return {"ok": False, "message": "Need a valid recipient address."}
        subject = (kwargs.get("subject") or "(no subject)").strip()
        body = kwargs.get("body") or ""
        try:
            await asyncio.to_thread(
                _send, host, address, password, to, subject, body
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
        logger.info("send_email.sent", to=to)
        return {"ok": True, "to": to, "subject": subject, "sent": True}
