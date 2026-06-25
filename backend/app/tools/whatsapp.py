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
from app.tools.validators import valid_phone  # UX Spec §3.1: digit-count guard

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


def mask_phone(phone: str) -> str:
    """A read-aloud masked number, e.g. ``+971 ••• ••67`` — hides the middle.

    Used so the assistant can confirm a recipient by ear without exposing the
    full number on screen.
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 5:
        return "••" + digits[-2:]
    return f"+{digits[:3]} ••• ••{digits[-2:]}"


class SendWhatsAppTool(Tool):
    """Open WhatsApp with a pre-filled message to a contact (deep link)."""

    name = "send_whatsapp"
    description = (
        "Send a WhatsApp message — call this ONLY after you have a confirmed "
        "recipient. Pass: a phone_number if the user gave one; OR a contact_id "
        "from a previous resolve_contact result; OR a contact_name that the "
        "user has saved. If the user names a person you have NOT resolved yet, "
        "call resolve_contact FIRST (do not call this with an unknown name). "
        "Opens WhatsApp with the message ready; the user taps Send. ALWAYS "
        "confirm the recipient + message before calling."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Text to send."},
            "phone_number": {
                "type": "string",
                "description": "Recipient phone (digits, with country code if "
                "known). Use when the user gave a number directly.",
            },
            "contact_id": {
                "type": "string",
                "description": "Opaque id from a resolve_contact match (device "
                "contact). The phone opens WhatsApp using its local number.",
            },
            "contact_name": {
                "type": "string",
                "description": "Name of a contact the user SAVED in the app.",
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
        contact_id = (kwargs.get("contact_id") or "").strip()
        name = (kwargs.get("contact_name") or "").strip()

        # A device contact resolved earlier: the phone holds the real number, so
        # tell it to open WhatsApp by id (the number never reaches the server).
        if not phone and contact_id:
            logger.info("send_whatsapp.open_messaging", contact_id=contact_id)
            return {
                "ok": True,
                "action": "open_messaging",
                "platform": "whatsapp",
                "channel": "whatsapp",
                "contact_id": contact_id,
                "message": message,
                "status": "opening_whatsapp",
            }

        if not phone and name:
            contact = await repo.find_contact(
                ctx.session, query=name, user_id=ctx.user_id
            )
            if contact and contact.phone:
                phone = contact.phone
            elif ctx.recall_resolved and ctx.recall_resolved(name):
                # The model named someone we JUST resolved on the device but
                # forgot to pass the contact_id — recover it so the send works.
                recalled = ctx.recall_resolved(name)
                logger.info("send_whatsapp.recalled", contact_id=recalled)
                return {
                    "ok": True,
                    "action": "open_messaging",
                    "platform": "whatsapp",
                    "channel": "whatsapp",
                    "contact_id": recalled,
                    "message": message,
                    "status": "opening_whatsapp",
                }
            else:
                # Unknown name with no number/id — the model should have called
                # resolve_contact first. Report not-found (ok:False) so it asks
                # instead of ever claiming the message was sent.
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

        # CHANGED (UX Spec §3.1): validate the digit count, not just "non-empty".
        # The old check only rejected zero-digit input, so a single mis-heard
        # digit ("5") became a broken wa.me link. valid_phone normalizes to the
        # plain-international-digits form wa.me requires AND rejects implausible
        # lengths (<7 or >15 digits).
        ok_phone, clean = valid_phone(phone, settings.default_country_code)
        if not ok_phone:
            return {
                "ok": False,
                "message": (
                    "That number doesn't look complete — can you give the full "
                    "number with country code?"
                ),
            }

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
