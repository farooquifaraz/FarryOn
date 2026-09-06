"""``make_call`` tool: place a phone call.

Client-executed, exactly like the messaging tools — the backend never dials
anything. It returns a ``tel:`` target (or, for a contact the PHONE resolved,
the opaque contact id) and the phone puts the call through.

The call goes out for real, so the safeguards sit around it rather than in it:
the assistant must confirm the recipient before calling, a name is resolved on
the device and read back as a MASKED number first (the real one never reaches
the server), and the phone falls back to a filled-in dialer whenever the call
permission has not been granted — so a refusal costs a tap, never a silence.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.db import repo
from app.logging_conf import get_logger
from app.tools import ratelimit
from app.tools.base import Tool, ToolContext
from app.tools.validators import valid_phone

logger = get_logger(__name__)


class MakeCallTool(Tool):
    """Open the phone's dialer on a contact's number (deep link)."""

    name = "make_call"
    description = (
        "Call someone on the phone. Give the recipient: a phone number if the "
        "user said one, OTHERWISE just the person's NAME as contact_name — the "
        "phone looks the number up in the user's own contacts, so do NOT ask "
        "for a number you weren't given. Use for 'call X', 'phone X', 'ring "
        "X', 'dial this number'. The phone DIALS — so ALWAYS confirm who you "
        "are about to call and get a clear yes first. Say you are calling; "
        "never claim the other person has answered or that you can hear them."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "phone_number": {
                "type": "string",
                "description": "Number to call (with country code if known). "
                "Use when the user gave a number directly.",
            },
            "contact_id": {
                "type": "string",
                "description": "Opaque id from a resolve_contact match (device "
                "contact). The phone dials using its local number.",
            },
            "contact_name": {
                "type": "string",
                "description": "Name of a contact the user SAVED in the app.",
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        phone = (kwargs.get("phone_number") or "").strip()
        contact_id = (kwargs.get("contact_id") or "").strip()
        name = (kwargs.get("contact_name") or "").strip()

        if not (phone or contact_id or name):
            return {"ok": False, "message": "Who should I call?"}

        # A runaway model must not be able to throw the dialer up eight times a
        # second — but dialling gets its OWN budget, not the message tools'.
        # Sharing one meant a user who had just sent a few texts could not phone
        # anyone, which is a bug wearing a safety feature's clothes.
        if not ratelimit.allow(f"call:{ctx.session_id}"):
            return {
                "ok": False,
                "status": "rate_limited",
                "message": (
                    "That's a lot of calls in a moment — let's pause before "
                    "dialling again."
                ),
            }

        # A contact the DEVICE resolved: we never saw the number, so the phone
        # dials it from the id it minted.
        if not phone and contact_id:
            logger.info("make_call.place_call", contact_id=contact_id)
            return {
                "ok": True,
                "action": "place_call",
                "channel": "call",
                "contact_id": contact_id,
                "status": "calling",
            }

        if not phone and name:
            contact = await repo.find_contact(
                ctx.session, query=name, user_id=ctx.user_id
            )
            if contact and contact.phone:
                phone = contact.phone
            elif ctx.recall_resolved and ctx.recall_resolved(name):
                # The model named someone it just resolved but didn't pass the
                # id — recover it rather than asking the user twice.
                recalled = ctx.recall_resolved(name)
                logger.info("make_call.recalled", contact_id=recalled)
                return {
                    "ok": True,
                    "action": "place_call",
                    "channel": "call",
                    "contact_id": recalled,
                    "status": "calling",
                }
            else:
                return {
                    "ok": False,
                    "status": "not_resolved",
                    "message": (
                        f"I haven't resolved {name} yet. Use resolve_contact "
                        "to find them, or ask the user for the number."
                    ),
                }

        ok_phone, clean = valid_phone(phone, get_settings().default_country_code)
        if not ok_phone:
            return {
                "ok": False,
                "message": (
                    "That number doesn't look complete — can you give the full "
                    "number with country code?"
                ),
            }

        logger.info("make_call.link", to=clean)
        return {
            "ok": True,
            "action": "open_url",
            "url": f"tel:+{clean}",
            "to": f"+{clean}",
            # The phone is dialling. Nobody has picked up, and the model
            # must not say otherwise.
            "status": "calling",
            "answered": False,
        }
