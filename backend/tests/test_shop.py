"""The glasses shop: a cart becomes a Stripe Checkout priced from OUR
catalog, a paid session becomes an order row exactly once, the operator is
mailed, and the admin can move it through shipped/delivered."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.core.responses import AppError
from app.db import base as db_base
from app.db.models import Order
from app.main import create_app
from app.modules.shop import service
from app.modules.shop.schemas import CartItem
from app.services import stripe_client
from app.core.security import hash_password
from app.db.models import User, UserRole
from app.db.seed import seed_roles_and_permissions
from app.web import products, shop

SECRET = "whsec_shop_test"
PASSWORD = "correct-horse-1"


async def _seed_user_with_role(email: str, role_name: str) -> None:
    async with db_base.get_sessionmaker()() as db:
        roles = await seed_roles_and_permissions(db)
        user = User(external_id=f"user:{email}", email=email, password_hash=hash_password(PASSWORD), status="active")
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=roles[role_name].id))
        await db.commit()


def _login(client: TestClient, email: str) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _setup_admin(client: TestClient, email: str) -> str:
    asyncio.run(_seed_user_with_role(email, "super_admin"))
    return _login(client, email)


def _register_user(client: TestClient, email: str) -> None:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})


def setup_module() -> None:
    os.environ["STRIPE_WEBHOOK_SECRET"] = SECRET
    get_settings.cache_clear()


def teardown_module() -> None:
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    get_settings.cache_clear()


def _sign(payload: bytes) -> str:
    t = int(time.time())
    v1 = hmac.new(SECRET.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


def _session(session_id: str = "cs_test_order1", *, kind: str = "glasses_order") -> dict:
    items = [
        {"slug": "gs5", "name": "GS5 MAX", "colour": "Red", "qty": 1, "price_aed": 450},
        {"slug": "l801", "name": "L801 Business", "colour": None, "qty": 2, "price_aed": 300},
    ]
    return {
        "id": session_id,
        "object": "checkout.session",
        "mode": "payment",
        "payment_status": "paid",
        "payment_intent": "pi_order1",
        "amount_total": 105000,
        "currency": "aed",
        "metadata": {"kind": kind, "items": json.dumps(items)},
        "customer_details": {
            "email": "buyer@example.com",
            "name": "Ayesha",
            "phone": "+971500000000",
            "address": {"line1": "Bill St", "city": "Dubai", "country": "AE"},
        },
        "collected_information": {
            "shipping_details": {
                "name": "Ayesha K",
                "address": {"line1": "12 Marina Walk", "line2": "Apt 4", "city": "Dubai", "postal_code": "00000", "country": "AE"},
            }
        },
    }


# ---- the page ------------------------------------------------------------------


def test_the_cards_price_and_buy_from_the_one_catalog() -> None:
    from pathlib import Path

    import app.web.router as web_router

    settings = get_settings()
    page = shop.render(Path(web_router._INDEX).read_text(encoding="utf-8"), settings)
    assert "<!--PRICE_AED:" not in page and "<!--BUY_ROW:" not in page and "<!--SHOP_CATALOG-->" not in page
    for slug, price in products.PRICES_AED.items():
        assert f'data-aed="{price}">AED {price}' in page, slug
        assert f'data-buy="{slug}"' in page and f"cartBuy('{slug}')" in page
    assert page.count("Buy now") == len(products.PRICES_AED)
    # GS5 offers its colours; the others do not
    assert '<select data-colour="gs5"' in page and page.count("<select data-colour=") == 1
    start = page.index('id="shop-catalog"')
    block = page[page.index(">", start) + 1 : page.index("</script>", start)]
    catalog = json.loads(block)
    assert {k: v["price_aed"] for k, v in catalog["items"].items()} == products.PRICES_AED
    assert catalog["items"]["gs5"]["colours"] == ["Black", "Red", "Cream"]
    assert "data-cart-count" in page and "function cartCheckout" in page


# ---- checkout ------------------------------------------------------------------


async def test_checkout_prices_the_cart_from_the_catalog_not_the_client(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    seen: list[dict] = []

    async def fake(**kw):
        seen.append(kw)
        return {"url": "https://checkout.stripe.test/cs_1"}

    monkeypatch.setattr(stripe_client, "create_order_session", fake)
    out = await service.create_checkout(
        items=[CartItem(slug="gs5", qty=2, colour="Cream"), CartItem(slug="l801", qty=1)],
        origin="http://localhost:8000",
    )
    assert out == {"url": "https://checkout.stripe.test/cs_1", "total_aed": 1200}
    kw = seen[-1]
    assert kw["line_items"][0]["price_data"]["unit_amount"] == 45000
    assert kw["line_items"][0]["price_data"]["currency"] == "aed"
    assert kw["line_items"][0]["price_data"]["product_data"]["name"] == "GS5 MAX — Cream"
    assert kw["line_items"][0]["quantity"] == 2
    assert kw["line_items"][1]["price_data"]["unit_amount"] == 30000
    assert kw["ship_to"] == ["AE"]
    assert kw["success_url"].startswith("http://localhost:8000/shop/success?session_id=")
    assert kw["cancel_url"] == "http://localhost:8000/#glasses"
    meta = kw["metadata"]
    assert meta["kind"] == "glasses_order"
    assert json.loads(meta["items"])[0] == {"slug": "gs5", "name": "GS5 MAX", "colour": "Cream", "qty": 2, "price_aed": 450}


async def test_checkout_refuses_unknown_models_and_colours(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    with pytest.raises(AppError) as err:
        await service.create_checkout(items=[CartItem(slug="gs9", qty=1)], origin="https://x")
    assert err.value.code == "UNKNOWN_MODEL"
    with pytest.raises(AppError) as err:
        await service.create_checkout(items=[CartItem(slug="gs5", qty=1, colour="Pink")], origin="https://x")
    assert err.value.code == "UNKNOWN_COLOUR"


def test_the_checkout_route_validates_and_answers_503_without_stripe(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "")
    client = TestClient(create_app())
    r = client.post("/api/v1/shop/checkout", json={"items": []})
    assert r.status_code == 422
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "gs5", "qty": 9}]})
    assert r.status_code == 422
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "gs5", "qty": 1, "colour": "Red"}]})
    assert r.status_code == 503 and r.json()["error"]["code"] == "SHOP_NOT_CONFIGURED"


# ---- the order ---------------------------------------------------------------


async def test_a_paid_session_becomes_one_order_and_mails_the_operator(db_session, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "shop_notify_email", "ops@example.com")
    mails: list[dict] = []
    monkeypatch.setattr(
        "app.modules.auth.notifications.send_outage_alert",
        lambda **kw: mails.append(kw),
    )
    first = await service.record_order(db_session, _session())
    assert first["duplicate"] is False and first["status"] == "paid"
    assert first["amount_cents"] == 105000 and first["currency"] == "AED"
    assert first["email"] == "buyer@example.com" and first["phone"] == "+971500000000"
    # the SHIPPING address wins over the billing one
    assert first["address"]["line1"] == "12 Marina Walk" and first["name"] == "Ayesha K"
    assert [i["qty"] for i in first["items"]] == [1, 2]
    assert mails and mails[-1]["to_email"] == "ops@example.com"
    assert "1 × GS5 MAX (Red)" in mails[-1]["text"] and "12 Marina Walk" in mails[-1]["text"]

    again = await service.record_order(db_session, _session())
    assert again["duplicate"] is True and again["id"] == first["id"]
    assert len(mails) == 1, "a redelivery must not mail twice"
    rows = (await db_session.execute(select(Order))).scalars().all()
    assert len(rows) == 1


def test_the_webhook_routes_a_glasses_order_to_the_shop_not_to_billing() -> None:
    client = TestClient(create_app())
    payload = json.dumps({"type": "checkout.session.completed", "data": {"object": _session("cs_wh_1")}}).encode()
    r = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _sign(payload), "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["duplicate"] is False and data["order_id"] >= 1
    r2 = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _sign(payload), "Content-Type": "application/json"},
    )
    assert r2.json()["data"] == {"order_id": data["order_id"], "duplicate": True}

    async def count() -> int:
        async with db_base.get_sessionmaker()() as db:
            return len((await db.execute(select(Order))).scalars().all())

    assert asyncio.run(count()) == 1


# ---- admin -------------------------------------------------------------------


def test_admins_list_orders_and_move_them_along() -> None:
    client = TestClient(create_app())
    admin_token = _setup_admin(client, "orders-admin@example.com")
    _register_user(client, "orders-plain@example.com")
    plain = _login(client, "orders-plain@example.com")

    async def seed() -> None:
        async with db_base.get_sessionmaker()() as db:
            await service.record_order(db, _session("cs_admin_1"))

    asyncio.run(seed())
    r = client.get("/api/v1/admin/orders", headers=_auth(admin_token))
    assert r.status_code == 200 and r.json()["meta"]["total"] == 1
    order = r.json()["data"][0]
    assert order["status"] == "paid" and order["items"][0]["name"] == "GS5 MAX"

    r = client.patch(f"/api/v1/admin/orders/{order['id']}", headers=_auth(admin_token), json={"status": "shipped", "note": "Aramex 123"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "shipped" and r.json()["data"]["note"] == "Aramex 123"
    r = client.get("/api/v1/admin/orders?status=paid", headers=_auth(admin_token))
    assert r.json()["meta"]["total"] == 0
    r = client.patch(f"/api/v1/admin/orders/{order['id']}", headers=_auth(admin_token), json={"status": "lost"})
    assert r.status_code == 422
    r = client.get("/api/v1/admin/orders", headers=_auth(plain))
    assert r.status_code == 403


# ---- the thank-you page ------------------------------------------------------------


def test_the_success_page_reads_the_session_from_stripe(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")

    async def fake(**kw):
        assert kw["session_id"] == "cs_paid"
        return _session("cs_paid")

    monkeypatch.setattr(stripe_client, "retrieve_checkout_session", fake)
    client = TestClient(create_app())
    r = client.get("/shop/success?session_id=cs_paid")
    assert r.status_code == 200
    assert "Order received" in r.text and "1 × GS5 MAX (Red)" in r.text and "AED 1050" in r.text
    # a junk id never breaks the page
    r = client.get("/shop/success?session_id=<script>")
    assert r.status_code == 200 and "Thank you" in r.text and "<script>" not in r.text.split("<body>")[1]
