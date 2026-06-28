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
import re
from typing import Any

import httpx

from app.config import get_settings
from app.db import repo
from app.logging_conf import get_logger
from app.services import telegram_user
from app.tools.base import Tool, ToolContext
from app.tools.idempotency import already_sent, mark_sent  # UX Spec §3.4
from app.tools.safety import rate_gate, sensitive_gate

logger = get_logger(__name__)

# Telegram usernames: 5-32 chars, start with a letter, then letters/digits/_.
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def valid_username(handle: str) -> bool:
    """Whether ``handle`` (without @) is a syntactically valid TG username."""
    return bool(_USERNAME_RE.match(handle or ""))
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
        "Send a Telegram message. Provide the message and a recipient: a "
        "@username, a phone number, or a contact_name (saved, or one you just "
        "resolved with resolve_contact). With the user's Telegram account set "
        "up it DELIVERS to anyone (no /start needed); otherwise it sends via the "
        "bot (if they started it) or opens the chat. Use for 'Telegram karo', "
        "'TG bhejo'. ALWAYS confirm the recipient and message first."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Text to send."},
            "username": {
                "type": "string",
                "description": "Telegram @username (with or without @).",
            },
            "phone_number": {
                "type": "string",
                "description": "Recipient phone (with country code) if given.",
            },
            "contact_name": {
                "type": "string",
                "description": "Name of a saved/resolved contact to look up.",
            },
            "group": {
                "type": "string",
                "description": "Name or @username of a Telegram GROUP or CHANNEL "
                "to post to (the user must be a member). Use this instead of a "
                "person when the user says 'group' or 'channel'.",
            },
            "confirm_sensitive": {
                "type": "boolean",
                "description": "True ONLY after the user explicitly confirmed "
                "sending a message flagged sensitive (OTP, password, card).",
            },
        },
        "required": ["message"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        message = (kwargs.get("message") or "").strip()
        if not message:
            return {"ok": False, "message": "What should the message say?"}

        blocked = sensitive_gate(message, bool(kwargs.get("confirm_sensitive")), ctx.session_id)
        if blocked:
            return blocked
        blocked = rate_gate(ctx.session_id)
        if blocked:
            return blocked

        settings = get_settings()
        username = (kwargs.get("username") or "").strip().lstrip("@")
        phone = (kwargs.get("phone_number") or "").strip()
        name = (kwargs.get("contact_name") or "").strip()
        group = (kwargs.get("group") or "").strip()
        chat_id: str | None = None

        # GROUP / CHANNEL: only the user's own account (MTProto) can post to a
        # group/channel they're a member of.
        if group:
            if not telegram_user.is_configured(settings):
                return {
                    "ok": False, "status": "account_needed",
                    "message": (
                        "Posting to a Telegram group/channel needs your "
                        "Telegram account connected. Set that up first."
                    ),
                }
            res = await telegram_user.user_send(
                settings, message=message, group=group
            )
            if res.get("ok"):
                logger.info("send_telegram.group_sent", to=res.get("to"))
                return {
                    "ok": True, "sent": True, "delivered": True,
                    "platform": "telegram", "via": "account",
                    "channel": "telegram", "to": res.get("to"),
                    "message": message,
                }
            reason = res.get("reason")
            msg = {
                "group_not_found": (
                    f"I couldn't find a group or channel called '{group}' that "
                    "you're in. What's its exact name?"
                ),
                "cannot_post": (
                    f"You don't have permission to post in '{group}'."
                ),
            }.get(reason, "I couldn't post to that group just now.")
            return {"ok": False, "status": reason or "group_failed",
                    "message": msg}

        if name and not username and not phone:
            contact = await repo.find_contact(
                ctx.session, query=name, user_id=ctx.user_id
            )
            if contact:
                chat_id = contact.telegram_chat_id
                username = (contact.telegram_username or "").lstrip("@")
                phone = contact.phone or ""
            # A device-resolved phone (from resolve_contact) lets the user's own
            # account message anyone in their contacts.
            if not username and not phone and ctx.recall_phone:
                phone = ctx.recall_phone(name) or ""
            if not chat_id and not username and not phone:
                return {
                    "ok": False,
                    "status": "not_resolved",
                    "message": (
                        f"I haven't resolved {name}'s Telegram yet — use "
                        "resolve_contact, or give their @username or number."
                    ),
                }

        # 1) BEST: send from the user's OWN Telegram account (MTProto). Delivers
        # to ANYONE (by @username or phone) — no /start, no manual paste.
        if telegram_user.is_configured(settings) and (username or phone):
            mt_phone = None
            if phone:
                d = re.sub(r"\D", "", phone)
                mt_phone = "+" + d if d else None
            res = await telegram_user.user_send(
                settings, message=message,
                username=username or None, phone=mt_phone,
            )
            if res.get("ok"):
                logger.info("send_telegram.user_sent", to=res.get("to"))
                return {
                    "ok": True, "sent": True, "delivered": True,
                    "platform": "telegram", "via": "account",
                    "to": res.get("to"), "message": message,
                }
            reason = res.get("reason")
            if reason in ("username_not_found", "not_on_telegram"):
                who = f"@{username}" if username else "that number"
                return {
                    "ok": False, "status": reason,
                    "message": (
                        f"{who} doesn't seem to be on Telegram, or I can't reach "
                        "them. Double-check the @username or number?"
                    ),
                }
            # not_authorized / unexpected -> fall through to bot/deep-link.
            logger.warning("send_telegram.user_failed", reason=reason)

        token = settings.telegram_bot_token

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

        # Fallback: open the chat. Telegram (unlike WhatsApp) does NOT let a
        # link pre-fill the text, so the only way to truly DELIVER is the Bot
        # API above. Here we open the chat and hand the message to the app to
        # copy to the clipboard, so the user just long-press → Paste → Send.
        if username:
            if not valid_username(username):
                return {
                    "ok": False,
                    "status": "invalid_username",
                    "message": (
                        f"'@{username}' doesn't look like a valid Telegram "
                        "username (5-32 letters/digits/underscores). Could you "
                        "confirm their exact @username?"
                    ),
                }
            return {
                "ok": True,
                "action": "open_url",
                "platform": "telegram",
                "url": f"https://t.me/{username}",
                "to": f"@{username}",
                "message": message,
                "copy_to_clipboard": message,
                "status": "opening_telegram",
                "delivered": False,
                "note": (
                    "Telegram links can't pre-fill text — opened the chat and "
                    "copied the message to the clipboard, so the user pastes + "
                    "sends. For true auto-send, set up the Telegram bot."
                ),
            }
        return {
            "ok": False,
            "message": "I need a Telegram @username or a saved contact to send.",
        }
