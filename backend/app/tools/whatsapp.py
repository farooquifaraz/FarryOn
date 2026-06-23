"""``send_whatsapp`` tool: message a contact on WhatsApp.

Phase 1 uses a free **wa.me deep link** — the backend returns ``action:
open_url`` and the app opens WhatsApp with the message pre-filled (the user taps
Send once). This works from each user's OWN personal WhatsApp, needs no API key,
and respects WhatsApp's Terms of Service. If a WhatsApp Business token is later
configured it can be extended to fully-automated sends.

The phone number can be given directly or resolved from a saved contact by name.
The model must confirm the recipient + message before calling this (enforced by
the system prompt's confirm-before-acting rule).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from app.config import get_settings
from app.db import repo
from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext

logger = get_logger(__name__)


def normalize_phone(phone: str, default_cc: str) -> str:
    """Digits-only E.164-ish number with a country code (no '+')."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    # If it already starts with a country code keep it; else prepend default and
    # drop a leading national 0.
    if digits.startswith(default_cc):
        return digits
    return default_cc + digits.lstrip("0")


class SendWhatsAppTool(Tool):
    """Open WhatsApp with a pre-filled message to a contact (deep link)."""

    name = "send_whatsapp"
    description = (
        "Send a WhatsApp message to someone. Provide the message and either a "
        "phone number (with country code if you have it) or a saved contact "
        "name. Use for 'WhatsApp karo', 'WA bhejo', 'message on WhatsApp'. "
        "Opens WhatsApp with the message ready; the user taps Send. ALWAYS "
        "confirm the recipient and message first."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Text to send."},
            "phone_number": {
                "type": "string",
                "description": "Recipient phone (digits, with country code if "
                "known). Optional if contact_name is given.",
            },
            "contact_name": {
                "type": "string",
                "description": "Name of a saved contact to look up the number.",
            },
        },
        "required": ["message"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        message = (kwargs.get("message") or "").strip()
        if not message:
            return {"ok": False, "message": "What should the message say?"}

        settings = get_settings()
        phone = (kwargs.get("phone_number") or "").strip()
        name = (kwargs.get("contact_name") or "").strip()

        if not phone and name:
            contact = await repo.find_contact(
                ctx.session, query=name, user_id=ctx.user_id
            )
            if contact and contact.phone:
                phone = contact.phone
            else:
                return {
                    "ok": False,
                    "message": (
                        f"I don't have a phone number for {name}. Ask the user "
                        "for it, then you can save it for next time."
                    ),
                }
        if not phone:
            return {
                "ok": False,
                "message": "I need the phone number to send a WhatsApp.",
            }

        clean = normalize_phone(phone, settings.default_country_code)
        if not clean:
            return {"ok": False, "message": "That phone number looks invalid."}

        url = f"https://wa.me/{clean}?text={quote(message)}"
        logger.info("send_whatsapp.link", to=clean)
        # Client-executed: the app opens this URL (WhatsApp) on the device.
        return {
            "ok": True,
            "action": "open_url",
            "platform": "whatsapp",
            "url": url,
            "to": f"+{clean}",
            "message": message,
            "status": "opening_whatsapp",
        }
