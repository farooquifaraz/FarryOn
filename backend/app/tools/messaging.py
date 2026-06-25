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
from app.tools.validators import valid_phone  # UX Spec §3.1: digit-count guard

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
                "Use when the user gave a number directly.",
            },
            "contact_id": {
                "type": "string",
                "description": "Opaque id from a resolve_contact match (device "
                "contact). The phone opens Messages using its local number.",
            },
            "contact_name": {
                "type": "string",
                "description": "Name of a contact the user SAVED in the app.",
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
        contact_id = (kwargs.get("contact_id") or "").strip()
        name = (kwargs.get("contact_name") or "").strip()

        # A device contact resolved earlier — let the phone open Messages by id
        # (the real number stays on the device).
        if not phone and contact_id:
            logger.info("send_message.open_messaging", contact_id=contact_id)
            return {
                "ok": True,
                "action": "open_messaging",
                "platform": "sms",
                "channel": "sms",
                "contact_id": contact_id,
                "message": text,
                "status": "opening_sms",
            }

        if not phone and name:
            contact = await repo.find_contact(
                ctx.session, query=name, user_id=ctx.user_id
            )
            if contact and contact.phone:
                phone = contact.phone
            elif ctx.recall_resolved and ctx.recall_resolved(name):
                # Recover a just-resolved device contact the model named but
                # didn't pass the id for.
                recalled = ctx.recall_resolved(name)
                logger.info("send_message.recalled", contact_id=recalled)
                return {
                    "ok": True,
                    "action": "open_messaging",
                    "platform": "sms",
                    "channel": "sms",
                    "contact_id": recalled,
                    "message": text,
                    "status": "opening_sms",
                }
            else:
                # Unknown name — the model should resolve_contact first. Report
                # not-found so it asks instead of claiming the text was sent.
                return {
                    "ok": False,
                    "status": "not_resolved",
                    "message": (
                        f"I haven't resolved {name} yet. Use resolve_contact "
                        "to find them, or ask the user for the number."
                    ),
                }
        if not phone:
            return {
                "ok": False,
                "message": "I need a phone number or a resolved contact.",
            }

        # CHANGED (UX Spec §3.1): digit-count validation, see whatsapp.py.
        ok_phone, clean = valid_phone(phone, settings.default_country_code)
        if not ok_phone:
            return {
                "ok": False,
                "message": (
                    "That number doesn't look complete — can you give the full "
                    "number with country code?"
                ),
            }

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
