"""``save_contact`` tool: remember a person's phone / Telegram for messaging.

Lets the user say "save Sara's number as +971..." so later "WhatsApp Sara" or
"Telegram Sara" resolves the destination automatically. Creating/updating a
contact is a change, so the model confirms first (system-prompt rule).
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.tools.base import Tool, ToolContext


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
