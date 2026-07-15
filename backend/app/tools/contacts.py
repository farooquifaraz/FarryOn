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
from app.services import telegram_user
from app.tools.base import Tool, ToolContext
from app.tools.validators import valid_phone  # UX Spec §3.1
from app.tools.whatsapp import mask_phone

logger = get_logger(__name__)

#: Most candidates to hand the model for an ambiguous match. Reading 17 names
#: aloud is unusable — in the field the user waited ~30s while the assistant
#: recited every "wife" in the phone book. The model gets the first few plus a
#: count so it can say "...and N more — say the exact name".
_MAX_OPTIONS = 6


def _ambiguous(
    channel: str, name: str, options: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build a size-capped ``ambiguous`` result with a 'be specific' hint."""
    shown, extra = options[:_MAX_OPTIONS], max(0, len(options) - _MAX_OPTIONS)
    msg = f"There are several matches for '{name}'. Read these out and ask which one"
    if extra:
        msg += (
            f" — and mention there are {extra} more, so they can say the exact name"
        )
    return {
        "ok": True,
        "status": "ambiguous",
        "channel": channel,
        "name": name,
        "options": shown,
        "more": extra,
        "message": msg + ".",
    }


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
            settings = get_settings()
            if saved and (saved.telegram_chat_id or saved.telegram_username):
                has_bot = bool(
                    settings.telegram_bot_token and saved.telegram_chat_id
                )
                return {
                    "ok": True,
                    "status": "found",
                    "channel": "telegram",
                    "name": saved.name,
                    "contact_name": saved.name,
                    "via": "bot" if has_bot else "deeplink",
                }
            # With the user's own Telegram account (MTProto) we can message
            # ANYONE by phone — so resolve the number from the device contacts
            # just like WhatsApp. The orchestrator caches the real phone for
            # send_telegram.
            if (
                telegram_user.is_configured(settings)
                and ctx.resolve_contact is not None
            ):
                try:
                    res = await ctx.resolve_contact(name, channel)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("resolve_contact.tg_bridge", error=repr(exc))
                    res = None
                if isinstance(res, dict):
                    status = (res.get("status") or "").lower()
                    cands = res.get("candidates") or []
                    if status == "found" and len(cands) == 1:
                        c0 = cands[0]
                        return {
                            "ok": True, "status": "found",
                            "channel": "telegram",
                            "name": c0.get("displayName") or name,
                            "contact_name": c0.get("displayName") or name,
                            "masked_number": c0.get("maskedNumber"),
                            # Hand this straight to send_telegram — the robust
                            # handle. A name can mismatch (the device's
                            # "Beautiful Wife🌹" vs the spoken "beautiful wife");
                            # an id can't.
                            "contact_id": c0.get("contactId"),
                            "via": "account",
                        }
                    if status == "ambiguous" or len(cands) > 1:
                        return _ambiguous("telegram", name, [
                            {"name": c.get("displayName"),
                             "masked_number": c.get("maskedNumber"),
                             "contact_id": c.get("contactId")}
                            for c in cands
                        ])
            # P2: not in the phone's contacts — search the user's OWN Telegram
            # contacts by name (someone added on Telegram but not in the phone).
            if telegram_user.is_configured(settings):
                tg = await telegram_user.find_contacts(settings, name)
                if len(tg) == 1:
                    c = tg[0]
                    if c.get("phone") and ctx.note_phone:
                        ctx.note_phone(name, c["phone"])  # send_telegram uses it
                    return {
                        "ok": True, "status": "found", "channel": "telegram",
                        "name": c.get("display") or name,
                        "contact_name": c.get("display") or name,
                        "username": c.get("username"),
                        "via": "account",
                    }
                if len(tg) > 1:
                    return _ambiguous("telegram", name, [
                        {"name": c.get("display"),
                         "username": c.get("username")}
                        for c in tg
                    ])
            return {
                "ok": True,
                "status": "not_found",
                "channel": "telegram",
                "name": name,
                "message": (
                    f"No Telegram contact found for {name}. Give their "
                    "@username or phone number."
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
            return _ambiguous(channel, name, [
                {
                    "name": c.get("displayName"),
                    "masked_number": c.get("maskedNumber"),
                    "contact_id": c.get("contactId"),
                }
                for c in candidates
            ])
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
