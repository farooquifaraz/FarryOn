"""``send_message`` tool: send a plain SMS text to a contact.

Like :mod:`app.tools.whatsapp`, this is **client-executed**: the backend returns
``action: open_url`` with an ``sms:`` deep link and the phone opens its Messages
app with the number and text pre-filled (the user taps Send). The recipient can
be given as a phone number, or as a name that the phone resolves from its own
contacts (``action: resolve_contact``) — exactly like WhatsApp. The model must
confirm the recipient + text first (enforced by the system prompt).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.config import get_settings
from app.db import repo
from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext
from app.tools.whatsapp import normalize_phone

logger = get_logger(__name__)


class SendMessageTool(Tool):
    """Open the SMS app with a pre-filled text to a contact (deep link)."""

    name = "send_message"
    description = (
        "Send a normal SMS text message to someone. Provide the text and the "
        "recipient: a phone number if the user gave one, OTHERWISE just pass "
        "the person's NAME as contact_name — the phone looks the number up in "
        "the user's own contacts automatically, so do NOT ask for a number you "
        "weren't given. Use for 'text X', 'send an SMS', 'message X' when no "
        "app (WhatsApp/Telegram) is named. Opens the Messages app with the text "
        "ready; the user taps Send. ALWAYS confirm the recipient and text first."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Message body to send."},
            "phone_number": {
                "type": "string",
                "description": "Recipient phone (with country code if known). "
                "Optional if contact_name is given.",
            },
            "contact_name": {
                "type": "string",
                "description": "Name of the person to look up in contacts.",
            },
        },
        "required": ["text"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        text = (kwargs.get("text") or "").strip()
        if not text:
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
                # Let the phone resolve the number from its own contacts and
                # open the SMS app (contacts never leave the device).
                logger.info("send_message.resolve_contact", name=name)
                return {
                    "ok": True,
                    "action": "resolve_contact",
                    "platform": "sms",
                    "name": name,
                    "message": text,
                    "status": "looking_up_contact",
                }
        if not phone:
            return {
                "ok": False,
                "message": "I need a phone number or a contact name to text.",
            }

        clean = normalize_phone(phone, settings.default_country_code)
        if not clean:
            return {"ok": False, "message": "That phone number looks invalid."}

        url = f"sms:+{clean}?body={quote(text)}"
        logger.info("send_message.link", to=clean)
        return {
            "ok": True,
            "action": "open_url",
            "platform": "sms",
            "url": url,
            "to": f"+{clean}",
            "message": text,
            "status": "opening_sms",
        }
