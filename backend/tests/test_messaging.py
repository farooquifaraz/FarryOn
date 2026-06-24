"""Tests for the WhatsApp / Telegram / contacts messaging tools."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import telegram as tg_mod
from app.tools.base import ToolContext
from app.tools.contacts import SaveContactTool
from app.tools.telegram import SendTelegramTool
from app.tools.whatsapp import SendWhatsAppTool, normalize_phone

pytestmark = pytest.mark.asyncio


def test_normalize_phone():
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


async def test_whatsapp_unknown_name_defers_to_device(db_session):
    """A name with no saved number asks the phone to resolve it on-device."""
    res = await SendWhatsAppTool().run(
        ToolContext(session=db_session), message="hi", contact_name="Sara",
    )
    assert res["ok"] is True
    assert res["action"] == "resolve_contact"
    assert res["name"] == "Sara"
    assert res["message"] == "hi"


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


async def test_telegram_needs_target(db_session, monkeypatch):
    monkeypatch.setattr(
        tg_mod, "get_settings",
        lambda: SimpleNamespace(telegram_bot_token=None),
    )
    res = await SendTelegramTool().run(
        ToolContext(session=db_session), message="hi"
    )
    assert res["ok"] is False
