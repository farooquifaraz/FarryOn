"""``send_telegram`` tool: message a contact on Telegram.

Two paths, chosen automatically:

* **Bot API (automatic)** — when ``TELEGRAM_BOT_TOKEN`` is set AND we have the
  recipient's numeric ``chat_id`` (saved when they started the FarryOn bot via
  the ``/webhook/telegram`` ``/start`` flow). The message is delivered directly.
* **Deep link (fallback)** — otherwise the backend returns ``action: open_url``
  with a ``t.me/<username>`` link the app opens; the user types/sends it
  themselves. (Telegram bots can't message arbitrary users who haven't started
  the bot, and t.me links can't pre-fill text — this is a Telegram limitation.)

Confirm the recipient + message before calling (system-prompt rule).
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from app.config import get_settings
from app.db import repo
from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext
from app.tools.idempotency import already_sent, mark_sent  # UX Spec §3.4

logger = get_logger(__name__)
_API = "https://api.telegram.org/bot{token}/sendMessage"
_HTTP_TIMEOUT = 10.0


async def _bot_send(token: str, chat_id: str, message: str) -> dict[str, Any]:
    """Send via the Telegram Bot API; returns the parsed response."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            _API.format(token=token),
            json={"chat_id": chat_id, "text": message},
        )
    return resp.json()


class SendTelegramTool(Tool):
    """Send a Telegram message (Bot API) or open the chat (deep link)."""

    name = "send_telegram"
    description = (
        "Send a Telegram message to someone. Provide the message and either a "
        "Telegram @username or a saved contact name. Use for 'Telegram karo', "
        "'TG bhejo', 'message on Telegram'. Sends automatically if the person "
        "has connected the bot, otherwise opens their Telegram chat. ALWAYS "
        "confirm the recipient and message first."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Text to send."},
            "username": {
                "type": "string",
                "description": "Telegram @username (with or without @).",
            },
            "contact_name": {
                "type": "string",
                "description": "Name of a saved contact to look up.",
            },
        },
        "required": ["message"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        message = (kwargs.get("message") or "").strip()
        if not message:
            return {"ok": False, "message": "What should the message say?"}

        username = (kwargs.get("username") or "").strip().lstrip("@")
        name = (kwargs.get("contact_name") or "").strip()
        chat_id: str | None = None

        if name and not username:
            contact = await repo.find_contact(
                ctx.session, query=name, user_id=ctx.user_id
            )
            if contact:
                chat_id = contact.telegram_chat_id
                username = (contact.telegram_username or "").lstrip("@")
            if not chat_id and not username:
                return {
                    "ok": False,
                    "message": (
                        f"I don't have a Telegram handle for {name}. Ask for "
                        "their @username."
                    ),
                }

        token = get_settings().telegram_bot_token

        # Automatic send when we have a token + the recipient's chat_id.
        if token and chat_id:
            # CHANGED (UX Spec §3.4): idempotency. The bot path is a REAL send,
            # so a retried turn could deliver the same Telegram message twice.
            # A fingerprint of chat_id+message suppresses an identical resend.
            fingerprint = (
                "tg:" + chat_id + ":"
                + hashlib.sha1(message.encode("utf-8")).hexdigest()
            )
            if already_sent(fingerprint):
                logger.info("send_telegram.deduped", chat_id=chat_id)
                return {"ok": True, "platform": "telegram", "to": chat_id,
                        "message": message, "sent": True, "deduped": True}
            try:
                data = await _bot_send(token, chat_id, message)
            except Exception as exc:  # noqa: BLE001
                logger.warning("send_telegram.failed", error=str(exc))
                return {"ok": False, "message": "Couldn't send on Telegram."}
            if data.get("ok"):
                mark_sent(fingerprint)  # block an identical resend
                logger.info("send_telegram.sent", chat_id=chat_id)
                return {"ok": True, "platform": "telegram", "to": chat_id,
                        "message": message, "sent": True}
            desc = data.get("description", "")
            if "blocked" in desc.lower() or "403" in desc:
                return {"ok": False, "message": "They've blocked the bot."}
            # fall through to deep link

        # Fallback: open the chat (user sends manually).
        if username:
            return {
                "ok": True,
                "action": "open_url",
                "platform": "telegram",
                "url": f"https://t.me/{username}",
                "to": f"@{username}",
                "message": message,
                "status": "opening_telegram",
                "note": "Opened the chat — Telegram can't pre-fill the text, so "
                "the user pastes/types it.",
            }
        return {
            "ok": False,
            "message": "I need a Telegram @username or a saved contact to send.",
        }
