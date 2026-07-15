"""Tests for the WhatsApp / Telegram / contacts messaging tools."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import telegram as tg_mod
from app.tools.base import ToolContext
from app.tools.contacts import ResolveContactTool, SaveContactTool
from app.tools.telegram import SendTelegramTool
from app.tools.whatsapp import SendWhatsAppTool, mask_phone, normalize_phone

pytestmark = pytest.mark.asyncio


async def test_normalize_phone():  # async to match this module's asyncio mark
    assert normalize_phone("+971 50 123 4567", "971") == "971501234567"
    assert normalize_phone("0501234567", "971") == "971501234567"
    assert normalize_phone("971501234567", "971") == "971501234567"
    assert normalize_phone("", "971") == ""


async def test_whatsapp_with_phone(db_session):
    res = await SendWhatsAppTool().run(
        ToolContext(session=db_session),
        message="Kal milte hain", phone_number="+971501234567",
    )
    assert res["ok"] is True
    assert res["action"] == "open_url"
    assert res["url"].startswith("https://wa.me/971501234567?text=")
    assert "Kal" in res["url"]


async def test_whatsapp_needs_number(db_session):
    res = await SendWhatsAppTool().run(
        ToolContext(session=db_session), message="hi"
    )
    assert res["ok"] is False


async def test_mask_phone():
    assert mask_phone("+971501234567").endswith("67")
    assert "•" in mask_phone("+971501234567")
    assert mask_phone("12") == "••12"


async def test_whatsapp_unknown_name_not_resolved(db_session):
    """An unresolved name returns ok:false so the model never claims 'sent'."""
    res = await SendWhatsAppTool().run(
        ToolContext(session=db_session), message="hi", contact_name="Sara",
    )
    assert res["ok"] is False
    assert res["status"] == "not_resolved"


async def test_whatsapp_contact_id_opens_on_device(db_session):
    """A resolved device contact_id yields an open_messaging action."""
    res = await SendWhatsAppTool().run(
        ToolContext(session=db_session), message="hi", contact_id="c_abc",
    )
    assert res["ok"] is True
    assert res["action"] == "open_messaging"
    assert res["contact_id"] == "c_abc"
    assert res["channel"] == "whatsapp"


async def test_whatsapp_recalls_resolved_id(db_session):
    """If the model names a just-resolved contact without the id, recover it."""
    ctx = ToolContext(
        session=db_session,
        recall_resolved=lambda n: "c_recalled" if n.lower() == "kamlesh" else None,
    )
    res = await SendWhatsAppTool().run(ctx, message="hi", contact_name="Kamlesh")
    assert res["ok"] is True
    assert res["action"] == "open_messaging"
    assert res["contact_id"] == "c_recalled"


async def test_resolve_telegram_account_contact(db_session, monkeypatch):
    """P2: a Telegram-only contact (not in phone) is found by name + cached."""
    import app.tools.contacts as contacts_mod

    monkeypatch.setattr(
        contacts_mod.telegram_user, "is_configured", lambda s: True
    )

    async def fake_find(settings, name):
        return [{"display": "Zaheer Abbas", "username": "zulu", "phone": "+9715"}]

    monkeypatch.setattr(contacts_mod.telegram_user, "find_contacts", fake_find)
    cached: dict[str, str] = {}
    ctx = ToolContext(
        session=db_session, note_phone=lambda n, p: cached.__setitem__(n, p)
    )
    res = await ResolveContactTool().run(ctx, name="Zaheer", channel="telegram")
    assert res["status"] == "found" and res["via"] == "account"
    assert cached == {"Zaheer": "+9715"}  # cached for send_telegram


async def test_resolve_telegram_account_ambiguous(db_session, monkeypatch):
    import app.tools.contacts as contacts_mod

    monkeypatch.setattr(
        contacts_mod.telegram_user, "is_configured", lambda s: True
    )

    async def fake_find(settings, name):
        return [
            {"display": "Ali A", "username": "alia", "phone": "+1"},
            {"display": "Ali B", "username": "alib", "phone": "+2"},
        ]

    monkeypatch.setattr(contacts_mod.telegram_user, "find_contacts", fake_find)
    res = await ResolveContactTool().run(
        ToolContext(session=db_session), name="Ali", channel="telegram"
    )
    assert res["status"] == "ambiguous" and len(res["options"]) == 2


async def test_resolve_telegram_not_found(db_session):
    res = await ResolveContactTool().run(
        ToolContext(session=db_session), name="Nobody", channel="telegram",
    )
    assert res["status"] == "not_found"
    assert res["channel"] == "telegram"


async def test_resolve_telegram_found_saved(db_session):
    from app.db import repo
    await repo.save_contact(
        db_session, name="Rahul", telegram_username="@rahul", user_id=None,
    )
    await db_session.commit()
    res = await ResolveContactTool().run(
        ToolContext(session=db_session), name="Rahul", channel="telegram",
    )
    assert res["status"] == "found"
    assert res["via"] == "deeplink"


async def test_resolve_whatsapp_saved_masked(db_session):
    from app.db import repo
    await repo.save_contact(
        db_session, name="Mom", phone="+971501112233", user_id=None,
    )
    await db_session.commit()
    res = await ResolveContactTool().run(
        ToolContext(session=db_session), name="Mom", channel="whatsapp",
    )
    assert res["status"] == "found"
    assert res["source"] == "saved"
    assert "•" in res["masked_number"]
    assert "contact_id" not in res  # saved -> no device id needed


async def test_resolve_whatsapp_device_found(db_session):
    async def fake_resolve(name, channel):
        return {
            "status": "found",
            "candidates": [
                {"contactId": "c1", "displayName": "Kamlesh",
                 "maskedNumber": "+971 ••• ••67"}
            ],
        }

    ctx = ToolContext(session=db_session, resolve_contact=fake_resolve)
    res = await ResolveContactTool().run(ctx, name="Kamlesh", channel="whatsapp")
    assert res["status"] == "found"
    assert res["source"] == "device"
    assert res["contact_id"] == "c1"
    assert res["masked_number"] == "+971 ••• ••67"


async def test_resolve_whatsapp_device_ambiguous(db_session):
    async def fake_resolve(name, channel):
        return {
            "status": "ambiguous",
            "candidates": [
                {"contactId": "c1", "displayName": "Kamlesh Home",
                 "maskedNumber": "+971 ••• ••67"},
                {"contactId": "c2", "displayName": "Kamlesh Office",
                 "maskedNumber": "+971 ••• ••88"},
            ],
        }

    ctx = ToolContext(session=db_session, resolve_contact=fake_resolve)
    res = await ResolveContactTool().run(ctx, name="Kamlesh", channel="whatsapp")
    assert res["status"] == "ambiguous"
    assert len(res["options"]) == 2


async def test_resolve_whatsapp_permission_denied(db_session):
    async def fake_resolve(name, channel):
        return {"status": "permission_denied"}

    ctx = ToolContext(session=db_session, resolve_contact=fake_resolve)
    res = await ResolveContactTool().run(ctx, name="X", channel="whatsapp")
    assert res["status"] == "permission_denied"


async def test_resolve_whatsapp_no_device_bridge(db_session):
    """Outside a live session (no bridge) it degrades, never crashes."""
    res = await ResolveContactTool().run(
        ToolContext(session=db_session), name="X", channel="whatsapp",
    )
    assert res["status"] == "index_unavailable"


async def test_save_contact_then_whatsapp(db_session):
    ctx = ToolContext(session=db_session)
    saved = await SaveContactTool().run(
        ctx, name="Sara", phone_number="+971509998888"
    )
    await db_session.commit()
    assert saved["ok"] is True
    assert saved["phone"] == "+971509998888"

    res = await SendWhatsAppTool().run(ctx, message="hello", contact_name="Sara")
    assert res["ok"] is True
    assert "971509998888" in res["url"]


async def test_save_contact_needs_a_handle(db_session):
    res = await SaveContactTool().run(ToolContext(session=db_session), name="Bob")
    assert res["ok"] is False


async def test_telegram_deeplink_without_token(db_session, monkeypatch):
    monkeypatch.setattr(
        tg_mod, "get_settings",
        lambda: SimpleNamespace(telegram_bot_token=None),
    )
    res = await SendTelegramTool().run(
        ToolContext(session=db_session), message="hi", username="@rahul",
    )
    assert res["ok"] is True
    assert res["action"] == "open_url"
    assert res["url"] == "https://t.me/rahul"
    # Honest: a deep link is NOT delivered; the message is copied to paste.
    assert res["delivered"] is False
    assert res["copy_to_clipboard"] == "hi"


async def test_telegram_group_send(db_session, monkeypatch):
    """A group name routes to the user-account group send (delivered)."""
    monkeypatch.setattr(tg_mod.telegram_user, "is_configured", lambda s: True)

    async def fake_user_send(settings, *, message, group=None, **kw):
        assert group == "Family"
        return {"ok": True, "to": "Family Group"}

    monkeypatch.setattr(tg_mod.telegram_user, "user_send", fake_user_send)
    monkeypatch.setattr(
        tg_mod, "get_settings", lambda: SimpleNamespace(telegram_bot_token=None)
    )
    res = await SendTelegramTool().run(
        ToolContext(session=db_session), message="hi all", group="Family",
    )
    assert res["ok"] is True and res["sent"] is True
    assert res["via"] == "account" and res["to"] == "Family Group"


async def test_telegram_group_not_found(db_session, monkeypatch):
    monkeypatch.setattr(tg_mod.telegram_user, "is_configured", lambda s: True)

    async def fake_user_send(settings, *, message, group=None, **kw):
        return {"ok": False, "reason": "group_not_found"}

    monkeypatch.setattr(tg_mod.telegram_user, "user_send", fake_user_send)
    monkeypatch.setattr(
        tg_mod, "get_settings", lambda: SimpleNamespace(telegram_bot_token=None)
    )
    res = await SendTelegramTool().run(
        ToolContext(session=db_session), message="hi", group="Nope",
    )
    assert res["ok"] is False and res["status"] == "group_not_found"


async def test_telegram_invalid_username_rejected(db_session, monkeypatch):
    monkeypatch.setattr(
        tg_mod, "get_settings",
        lambda: SimpleNamespace(telegram_bot_token=None),
    )
    res = await SendTelegramTool().run(
        ToolContext(session=db_session), message="hi", username="@x",  # too short
    )
    assert res["ok"] is False
    assert res["status"] == "invalid_username"


async def test_telegram_bot_send_with_token(db_session, monkeypatch):
    monkeypatch.setattr(
        tg_mod, "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="tok"),
    )

    async def fake_send(token, chat_id, message):
        assert token == "tok"
        return {"ok": True, "result": {"message_id": 5}}

    monkeypatch.setattr(tg_mod, "_bot_send", fake_send)

    # Save a contact that has a chat_id (as if onboarded).
    from app.db import repo
    await repo.upsert_telegram_chat(
        db_session, chat_id="12345", username="rahul", display_name="Rahul"
    )
    await db_session.commit()

    res = await SendTelegramTool().run(
        ToolContext(session=db_session), message="hi", contact_name="Rahul",
    )
    assert res["ok"] is True
    assert res["sent"] is True
    assert res["to"] == "12345"


async def test_telegram_contact_id_sends_via_account(db_session, monkeypatch):
    """The user picks a match from an ambiguous list -> that contact_id maps to
    the cached phone and the user-account (MTProto) path delivers it. Without
    this, contact_id was ignored and every device-contact send dead-ended on
    "I need a @username or a saved contact"."""
    monkeypatch.setattr(tg_mod.telegram_user, "is_configured", lambda s: True)

    captured: dict[str, str] = {}

    async def fake_user_send(settings, *, message, username=None, phone=None, **kw):
        captured["phone"] = phone
        return {"ok": True, "to": "Beautiful Wife"}

    monkeypatch.setattr(tg_mod.telegram_user, "user_send", fake_user_send)
    monkeypatch.setattr(
        tg_mod, "get_settings", lambda: SimpleNamespace(telegram_bot_token=None)
    )
    ctx = ToolContext(
        session=db_session,
        recall_phone_by_id=lambda cid: "+971501234567" if cid == "c0" else None,
    )
    res = await SendTelegramTool().run(ctx, message="Hi", contact_id="c0")
    assert res["ok"] is True and res["delivered"] is True
    assert res["via"] == "account" and res["to"] == "Beautiful Wife"
    assert captured["phone"] == "+971501234567"


async def test_resolve_ambiguous_is_capped(db_session):
    """A huge ambiguous match is trimmed (so the assistant doesn't recite 17
    names) but reports how many more there are."""
    big = [
        {"displayName": f"Wife {i}", "maskedNumber": "•••12",
         "contactId": f"c{i}"} for i in range(17)
    ]

    async def fake_resolve(name, channel):
        return {"status": "ambiguous", "candidates": big}

    ctx = ToolContext(session=db_session, resolve_contact=fake_resolve)
    res = await ResolveContactTool().run(ctx, name="wife", channel="whatsapp")
    assert res["status"] == "ambiguous"
    assert len(res["options"]) == 6   # capped
    assert res["more"] == 11          # ...and N more


async def test_resolve_telegram_device_single_returns_contact_id(
    db_session, monkeypatch
):
    """A single device match for telegram returns a contact_id, so the model
    sends by id (robust) instead of by a name that can mismatch."""
    import app.tools.contacts as contacts_mod

    monkeypatch.setattr(
        contacts_mod.telegram_user, "is_configured", lambda s: True
    )

    async def fake_resolve(name, channel):
        return {"status": "found", "candidates": [
            {"displayName": "Beautiful Wife🌹", "maskedNumber": "+971•••96",
             "contactId": "c0", "phone": "+971500000096"},
        ]}

    ctx = ToolContext(session=db_session, resolve_contact=fake_resolve)
    res = await ResolveContactTool().run(ctx, name="wife", channel="telegram")
    assert res["status"] == "found" and res["via"] == "account"
    assert res["contact_id"] == "c0"
    assert res["masked_number"] == "+971•••96"


async def test_telegram_needs_target(db_session, monkeypatch):
    monkeypatch.setattr(
        tg_mod, "get_settings",
        lambda: SimpleNamespace(telegram_bot_token=None),
    )
    res = await SendTelegramTool().run(
        ToolContext(session=db_session), message="hi"
    )
    assert res["ok"] is False
