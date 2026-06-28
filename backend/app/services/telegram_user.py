"""Send Telegram messages AS THE USER (MTProto via Telethon).

Unlike the bot path (which can only message people who /started the bot), the
user-account API delivers to ANYONE the user could message from the real
Telegram app — by @username or by phone number (a contact, or imported on the
fly). Requires a one-time login (see ``scripts/tg_login_live.py``) that yields
``TELEGRAM_SESSION``.

The client connects per-send and disconnects — simple and robust inside the
async server; a Telegram send is a one-off, not a hot path.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.logging_conf import get_logger

logger = get_logger(__name__)


def is_configured(settings: Settings) -> bool:
    """Whether the user-account (MTProto) path is set up and logged in."""
    return bool(
        getattr(settings, "telegram_api_id", None)
        and getattr(settings, "telegram_api_hash", None)
        and getattr(settings, "telegram_session", None)
    )


async def user_send(
    settings: Settings,
    *,
    message: str,
    phone: str | None = None,
    username: str | None = None,
) -> dict[str, Any]:
    """Send ``message`` from the user's own Telegram account.

    Resolve the recipient by ``username`` (preferred when given) or ``phone``
    (a saved contact, else imported on the fly). Returns ``{ok, ...}``; ``ok``
    is False with a ``reason`` for the expected failures so the tool can speak a
    friendly message.
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.contacts import ImportContactsRequest
    from telethon.tl.types import InputPhoneContact

    client = TelegramClient(
        StringSession(settings.telegram_session),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"ok": False, "reason": "not_authorized"}

        entity = None
        if username:
            handle = username if username.startswith("@") else "@" + username
            try:
                entity = await client.get_entity(handle)
            except Exception:  # noqa: BLE001 - unknown username
                return {"ok": False, "reason": "username_not_found"}
        elif phone:
            try:
                entity = await client.get_entity(phone)
            except Exception:  # noqa: BLE001 - not a saved contact yet
                imported = await client(
                    ImportContactsRequest([
                        InputPhoneContact(
                            client_id=0, phone=phone,
                            first_name="FarryOn", last_name="",
                        )
                    ])
                )
                if imported.users:
                    entity = imported.users[0]
                else:
                    return {"ok": False, "reason": "not_on_telegram"}

        if entity is None:
            return {"ok": False, "reason": "no_recipient"}

        await client.send_message(entity, message)
        name = getattr(entity, "first_name", None) or getattr(
            entity, "username", None
        ) or "them"
        logger.info("telegram_user.sent", to=name)
        return {"ok": True, "to": name}
    finally:
        await client.disconnect()
