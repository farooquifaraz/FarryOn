"""``save_contact`` tool: remember a person's phone / Telegram for messaging.

Lets the user say "save Sara's number as +971..." so later "WhatsApp Sara" or
"Telegram Sara" resolves the destination automatically. Creating/updating a
contact is a change, so the model confirms first (system-prompt rule).
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.db import repo
from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext
from app.tools.validators import valid_phone  # UX Spec §3.1
from app.tools.whatsapp import mask_phone

logger = get_logger(__name__)


class ResolveContactTool(Tool):
    """Find who to message BEFORE sending — read-only, no confirmation.

    Resolves a person's NAME to a messaging recipient so the assistant can
    confirm the right person (by a masked number) before it ever sends. For
    WhatsApp it first checks the user's app-saved contacts, then asks the phone
    to match its own device contacts (privacy-preserving: only a masked number +
    an opaque contact id come back). For Telegram it uses saved contacts only
    (device contacts don't carry Telegram handles).
    """

    name = "resolve_contact"
    description = (
        "Find a person to message BEFORE sending. Read-only, NO confirmation "
        "needed — call this FIRST whenever the user names someone to WhatsApp or "
        "Telegram and you don't already have their number/handle. Returns "
        "status=found with a MASKED number (read it back to confirm) and a "
        "contact_id to pass to send_whatsapp; or not_found / ambiguous / "
        "no_number / permission_denied so you can ask the user. NEVER say a "
        "message was sent based on this — it only looks up the recipient."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Person's name to find."},
            "channel": {
                "type": "string",
                "enum": ["whatsapp", "telegram", "sms"],
                "description": "Which app the user wants to message on.",
            },
        },
        "required": ["name"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        name = (kwargs.get("name") or "").strip()
        channel = (kwargs.get("channel") or "whatsapp").strip().lower()
        if channel not in ("whatsapp", "telegram", "sms"):
            channel = "whatsapp"
        if not name:
            return {"ok": True, "status": "not_found", "message": "Who?"}

        saved = await repo.find_contact(
            ctx.session, query=name, user_id=ctx.user_id
        )

        if channel == "telegram":
            # Device contacts don't hold Telegram handles — saved contacts only.
            if saved and (saved.telegram_chat_id or saved.telegram_username):
                has_bot = bool(
                    get_settings().telegram_bot_token and saved.telegram_chat_id
                )
                return {
                    "ok": True,
                    "status": "found",
                    "channel": "telegram",
                    "name": saved.name,
                    "contact_name": saved.name,
                    "via": "bot" if has_bot else "deeplink",
                }
            return {
                "ok": True,
                "status": "not_found",
                "channel": "telegram",
                "name": name,
                "message": (
                    f"No saved Telegram handle for {name}. Ask for their "
                    "@username."
                ),
            }

        # WhatsApp: saved contacts first (server already has that number).
        if saved and saved.phone:
            return {
                "ok": True,
                "status": "found",
                "channel": channel,
                "name": saved.name,
                "contact_name": saved.name,
                "masked_number": mask_phone(saved.phone),
                "source": "saved",
            }

        # Otherwise resolve on the device (number never reaches the server).
        if ctx.resolve_contact is None:
            return {"ok": True, "status": "index_unavailable", "name": name}

        # CHANGED (UX Spec §3.3): guard the device round-trip. The callback can
        # raise or time out, and a non-dict/None reply would crash on .get(...)
        # with an AttributeError that reaches the model as a raw stack string.
        # Any failure degrades to a clean "index_unavailable" so the assistant
        # just says "one sec" and retries instead of erroring.
        try:
            res = await ctx.resolve_contact(name, channel)
        except Exception as exc:  # noqa: BLE001 - device bridge is best-effort
            logger.warning("resolve_contact.bridge_failed", error=repr(exc))
            return {"ok": True, "status": "index_unavailable", "name": name}
        if not isinstance(res, dict):
            return {"ok": True, "status": "index_unavailable", "name": name}
        status = (res.get("status") or "index_unavailable").lower()
        candidates = res.get("candidates") or []

        if status == "found" and len(candidates) == 1:
            c = candidates[0]
            return {
                "ok": True,
                "status": "found",
                "channel": channel,
                "name": c.get("displayName") or name,
                "masked_number": c.get("maskedNumber"),
                "contact_id": c.get("contactId"),
                "source": "device",
            }
        if status == "ambiguous" or len(candidates) > 1:
            return {
                "ok": True,
                "status": "ambiguous",
                "channel": channel,
                "name": name,
                "options": [
                    {
                        "name": c.get("displayName"),
                        "masked_number": c.get("maskedNumber"),
                        "contact_id": c.get("contactId"),
                    }
                    for c in candidates
                ],
            }
        # not_found / no_number / permission_denied / index_unavailable
        return {
            "ok": True,
            "status": status if status in (
                "not_found", "no_number", "permission_denied",
                "index_unavailable",
            ) else "not_found",
            "channel": "whatsapp",
            "name": name,
        }


class SaveContactTool(Tool):
    """Save or update a contact's phone and/or Telegram handle."""

    name = "save_contact"
    description = (
        "Save a person's phone number and/or Telegram @username under a name, "
        "so the user can later say 'WhatsApp <name>' or 'Telegram <name>'. Use "
        "for 'save <name>'s number', 'add <name> to contacts'. Confirm first."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Contact's name."},
            "phone_number": {
                "type": "string",
                "description": "Phone number (with country code if known).",
            },
            "telegram_username": {
                "type": "string",
                "description": "Telegram @username.",
            },
        },
        "required": ["name"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        name = (kwargs.get("name") or "").strip()
        phone = (kwargs.get("phone_number") or "").strip() or None
        tg = (kwargs.get("telegram_username") or "").strip() or None
        if not name:
            return {"ok": False, "message": "What's the contact's name?"}
        if not phone and not tg:
            return {
                "ok": False,
                "message": "Give a phone number or a Telegram @username to save.",
            }
        # CHANGED (UX Spec §2.9 / §3.1): VALIDATE the phone before storing it, so
        # a junk number (a single mis-heard digit) is rejected up front instead
        # of being saved and only failing later at send time. We store the
        # number AS THE USER GAVE IT (e.g. "+971509998888") — the send tools
        # already normalize to plain digits via valid_phone — so the saved value
        # stays human-readable.
        if phone is not None:
            ok_phone, _clean = valid_phone(
                phone, get_settings().default_country_code
            )
            if not ok_phone:
                return {
                    "ok": False,
                    "message": (
                        "That number doesn't look complete — give the full "
                        "number with country code so I can save it correctly."
                    ),
                }
        contact = await repo.save_contact(
            ctx.session, name=name, phone=phone,
            telegram_username=tg, user_id=ctx.user_id,
        )
        return {
            "ok": True,
            "id": contact.id,
            "name": contact.name,
            "phone": contact.phone,
            "telegram_username": contact.telegram_username,
        }
