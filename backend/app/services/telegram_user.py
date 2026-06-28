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


async def _resolve_chat(client: Any, name: str) -> tuple[Any | None, list[str]]:
    """Resolve a GROUP or CHANNEL the user is IN, by title or @username.

    Returns ``(entity, ambiguous_titles)``. ``entity`` is set when there's a
    single clear match (exact title, or the only contains-match). When several
    different groups/channels contain the name and none is an exact title,
    ``entity`` is None and ``ambiguous_titles`` lists them so the caller can ask
    which one. Only an EXPLICIT ``@handle`` is looked up globally; a plain name
    matches the user's OWN dialogs only (never a random public @handle).
    """
    handle = name.strip()
    if handle.startswith("@"):
        try:
            return await client.get_entity(handle), []
        except Exception:  # noqa: BLE001 - no such public handle
            pass
    want = handle.lstrip("@").lower()
    exact = None
    contains: list[tuple[str, Any]] = []
    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue
        title = dialog.title or ""
        tl = title.lower()
        if tl == want:
            exact = dialog.entity
        elif want in tl:
            contains.append((title, dialog.entity))
    if exact is not None:
        return exact, []
    if len(contains) == 1:
        return contains[0][1], []
    if len(contains) > 1:
        return None, [t for t, _ in contains]
    return None, []


async def find_contacts(settings: Settings, name: str) -> list[dict[str, Any]]:
    """Search the user's OWN Telegram contacts by name (P2).

    Returns ``[{display, username, phone}]`` for contacts whose name or
    @username contains ``name`` — so a person who is a Telegram contact but not
    in the phone's contacts can still be found by name. Empty list on any error.
    """
    if not is_configured(settings) or not name.strip():
        return []
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.contacts import GetContactsRequest

    want = name.strip().lower()
    client = TelegramClient(
        StringSession(settings.telegram_session),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    out: list[dict[str, Any]] = []
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return []
        contacts = await client(GetContactsRequest(hash=0))
        for u in getattr(contacts, "users", []):
            full = " ".join(
                p for p in (
                    getattr(u, "first_name", "") or "",
                    getattr(u, "last_name", "") or "",
                ) if p
            ).strip()
            uname = getattr(u, "username", None)
            hay = f"{full} {uname or ''}".lower()
            if want in hay:
                out.append({
                    "display": full or uname or "Telegram user",
                    "username": uname,
                    "phone": getattr(u, "phone", None),
                })
    except Exception as exc:  # noqa: BLE001 - best-effort search
        logger.warning("telegram_user.find_contacts_failed", error=repr(exc))
        return []
    finally:
        await client.disconnect()
    return out


async def user_send(
    settings: Settings,
    *,
    message: str,
    phone: str | None = None,
    username: str | None = None,
    group: str | None = None,
) -> dict[str, Any]:
    """Send ``message`` from the user's own Telegram account.

    Recipient resolution order: ``group`` (a group/channel the user is in),
    then ``username``, then ``phone`` (a saved contact, else imported on the
    fly). Returns ``{ok, ...}``; ``ok`` is False with a ``reason`` for expected
    failures so the tool can speak a friendly message.
    """
    from telethon import TelegramClient
    from telethon.errors import ChatWriteForbiddenError
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
        if group:
            entity, ambiguous = await _resolve_chat(client, group)
            if ambiguous:
                return {"ok": False, "reason": "group_ambiguous",
                        "options": ambiguous}
            if entity is None:
                return {"ok": False, "reason": "group_not_found"}
        elif username:
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

        try:
            await client.send_message(entity, message)
        except ChatWriteForbiddenError:
            return {"ok": False, "reason": "cannot_post"}
        name = (
            getattr(entity, "title", None)
            or getattr(entity, "first_name", None)
            or getattr(entity, "username", None)
            or "them"
        )
        logger.info("telegram_user.sent", to=name)
        return {"ok": True, "to": name}
    finally:
        await client.disconnect()
