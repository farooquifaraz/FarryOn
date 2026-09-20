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
from app.core.security import hash_password
from app.db import base as db_base
from app.db.models import Order, User, UserRole
from app.db.seed import seed_roles_and_permissions
from app.main import create_app
from app.modules.shop import service
from app.modules.shop.schemas import CartItem, Customer
from app.services import stripe_client
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


CUSTOMER = Customer(
    email="buyer@example.com", name="Ayesha K", phone="+971 50 000 0000",
    line1="12 Marina Walk", line2="Apt 4", po_box="12345", city="Dubai", state="Dubai", country="ae",
)


def _session(session_id: str = "cs_test_order1", *, kind: str = "glasses_order", ours: bool = True) -> dict:
    items = [{"s": "gs5", "c": "Red", "q": 1, "p": 450}, {"s": "l801", "c": None, "q": 2, "p": 300}]
    meta = {"kind": kind, "items": json.dumps(items, separators=(",", ":"))}
    if ours:
        meta["customer"] = json.dumps({"email": "buyer@example.com", "name": "Ayesha K", "phone": "+971 50 000 0000"})
        meta["ship"] = json.dumps({"name": "Ayesha K", "line1": "12 Marina Walk", "line2": "Apt 4", "po_box": "12345",
                                   "city": "Dubai", "state": "Dubai", "postal_code": None, "country": "AE"})
    return {
        "id": session_id,
        "object": "checkout.session",
        "mode": "payment",
        "payment_status": "paid",
        "payment_intent": "pi_order1",
        "amount_total": 105000,
        "currency": "aed",
        "metadata": meta,
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


def test_the_cards_price_and_buy_from_the_one_catalog(monkeypatch) -> None:
    from pathlib import Path

    import app.web.router as web_router

    settings = get_settings()
    # the developer's .env may narrow the list; the test pins the default (everywhere)
    monkeypatch.setattr(settings, "shop_ship_countries", ["*"])
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
    assert [c["name"] for c in catalog["items"]["gs5"]["colours"]] == ["Black", "Red", "Cream"]
    assert all(c["in_stock"] for c in catalog["items"]["gs5"]["colours"])
    assert all(v["in_stock"] for v in catalog["items"].values())
    assert catalog["ship_to_names"][0] == "United Arab Emirates" and len(catalog["ship_to"]) >= 50
    assert catalog["items"]["gs5"]["prices"] == {"AED": 450, "INR": 11999, "USD": 129}
    assert catalog["currencies"] == ["AED", "INR", "USD"]
    assert catalog["delivery"]["AE"]["AED"] == 0 and catalog["delivery"]["IN"]["INR"] == 1500 and catalog["delivery"]["*"]["USD"] == 30
    # the picker offers the world, the countries with their own delivery price first
    assert catalog["countries"][:2] == [["AE", "United Arab Emirates"], ["IN", "India"]] and len(catalog["countries"]) >= 50
    assert 'data-inr="11999" data-usd="129"' in page, "the card carries its fixed rupee/dollar price"
    assert ["IN", "India"] in catalog["countries"] and ["US", "United States"] in catalog["countries"]
    assert catalog["country_zones"]["Asia/Dubai"] == "AE" and catalog["country_zones"]["Asia/Kolkata"] == "IN"
    assert catalog["regions"]["AE"][:2] == ["Abu Dhabi", "Dubai"] and len(catalog["regions"]["AE"]) == 7
    assert "Maharashtra" in catalog["regions"]["IN"] and "Delhi" in catalog["regions"]["IN"]
    assert "function cartGuessCountry" in page and "data-cart-nodeliver" in page
    assert "data-cart-count" in page and "function cartCheckout" in page
    # the drawer must really be closed on load: `hidden` has to beat display:flex
    assert ".cart[hidden]" in page and "<aside class=\"cart\" data-cart hidden" in page


def test_sold_out_models_and_colours_are_marked_and_refused(monkeypatch) -> None:
    from pathlib import Path

    import app.web.router as web_router

    settings = get_settings()
    monkeypatch.setattr(settings, "shop_out_of_stock", ["l802", "gs5:Red"])
    page = shop.render(Path(web_router._INDEX).read_text(encoding="utf-8"), settings)
    assert 'data-buy="l802" data-stock="out"' in page and page.count("Out of stock") == 1
    assert '<option value="Red" disabled>Red — out of stock</option>' in page
    assert '<option value="Black">Black</option>' in page
    start = page.index('id="shop-catalog"')
    catalog = json.loads(page[page.index(">", start) + 1 : page.index("</script>", start)])
    assert catalog["items"]["l802"]["in_stock"] is False
    assert {c["name"]: c["in_stock"] for c in catalog["items"]["gs5"]["colours"]} == {"Black": True, "Red": False, "Cream": True}

    async def refused() -> None:
        monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
        async with db_base.get_sessionmaker()() as db:
            with pytest.raises(AppError) as err:
                await service.create_checkout(db, items=[CartItem(slug="l802", qty=1)], customer=CUSTOMER, origin="https://x")
            assert err.value.code == "OUT_OF_STOCK"
            with pytest.raises(AppError) as err:
                await service.create_checkout(db, items=[CartItem(slug="gs5", qty=1, colour="Red")], customer=CUSTOMER, origin="https://x")
            assert err.value.code == "OUT_OF_STOCK"

    asyncio.run(refused())


def test_the_admin_toggles_stock_and_the_page_and_checkout_follow(monkeypatch) -> None:
    """Sold out from the admin panel, with no env edit and no restart."""
    client = TestClient(create_app())
    admin_token = _setup_admin(client, "stock-admin@example.com")
    r = client.get("/api/v1/admin/stock", headers=_auth(admin_token))
    assert r.status_code == 200
    keys = {row["key"]: row for row in r.json()["data"]}
    assert set(keys) == {"l801", "l802", "gs4", "gs5", "gs5:Black", "gs5:Red", "gs5:Cream"}
    assert all(row["in_stock"] for row in keys.values())
    assert keys["gs5:Red"] == {"key": "gs5:Red", "model": "GS5 MAX", "colour": "Red", "in_stock": True}

    r = client.put("/api/v1/admin/stock/gs5:Red", headers=_auth(admin_token), json={"in_stock": False})
    assert r.status_code == 200 and r.json()["data"]["in_stock"] is False
    r = client.put("/api/v1/admin/stock/l802", headers=_auth(admin_token), json={"in_stock": False})
    assert r.status_code == 200
    r = client.put("/api/v1/admin/stock/gs9", headers=_auth(admin_token), json={"in_stock": False})
    assert r.status_code == 404

    # the landing page greys them out
    page = client.get("/").text
    assert '<option value="Red" disabled>Red — out of stock</option>' in page
    assert 'data-buy="l802" data-stock="out"' in page

    # and the checkout refuses them
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "gs5", "qty": 1, "colour": "Red"}], "customer": CUSTOMER.model_dump()})
    assert r.status_code == 400 and r.json()["error"]["code"] == "OUT_OF_STOCK"

    # back on sale
    r = client.put("/api/v1/admin/stock/gs5:Red", headers=_auth(admin_token), json={"in_stock": True})
    assert r.json()["data"]["in_stock"] is True
    assert '<option value="Red">Red</option>' in client.get("/").text


# ---- checkout ------------------------------------------------------------------


async def test_checkout_prices_the_cart_from_the_catalog_not_the_client(db_session, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    seen: list[dict] = []

    async def fake(**kw):
        seen.append(kw)
        return {"url": "https://checkout.stripe.test/cs_1"}

    monkeypatch.setattr(stripe_client, "create_order_session", fake)
    out = await service.create_checkout(
        db_session,
        items=[CartItem(slug="gs5", qty=2, colour="Cream"), CartItem(slug="l801", qty=1)],
        customer=CUSTOMER,
        origin="http://localhost:8000",
    )
    assert out == {"url": "https://checkout.stripe.test/cs_1", "total": 1200, "currency": "AED", "delivery": 0}
    kw = seen[-1]
    assert kw["line_items"][0]["price_data"]["unit_amount"] == 45000
    assert kw["line_items"][0]["price_data"]["currency"] == "aed"
    assert kw["line_items"][0]["price_data"]["product_data"]["name"] == "GS5 MAX — Cream"
    assert kw["line_items"][0]["quantity"] == 2
    assert kw["line_items"][1]["price_data"]["unit_amount"] == 30000
    assert len(kw["line_items"]) == 2, "delivery within the UAE is free — no line"
    # our cart took the address: Stripe only takes the card, prefilled email
    assert kw.get("ship_to") is None and kw["customer_email"] == "buyer@example.com"
    assert kw["success_url"] == "http://localhost:8000/?order={CHECKOUT_SESSION_ID}#glasses"
    assert kw["cancel_url"] == "http://localhost:8000/?cart=cancelled#glasses"
    meta = kw["metadata"]
    assert meta["kind"] == "glasses_order"
    assert json.loads(meta["items"])[0] == {"s": "gs5", "c": "Cream", "q": 2, "p": 450}
    assert meta["currency"] == "AED" and meta["delivery"] == "0"
    assert json.loads(meta["customer"]) == {"email": "buyer@example.com", "name": "Ayesha K", "phone": "+971 50 000 0000"}
    assert json.loads(meta["ship"])["line1"] == "12 Marina Walk" and json.loads(meta["ship"])["country"] == "AE"
    assert all(len(v) <= 500 for v in meta.values()), "Stripe caps a metadata value at 500 characters"
    # ten of everything still fits the cap
    big = [CartItem(slug="gs5", qty=5, colour="Black")] * 10
    await service.create_checkout(db_session, items=big, customer=CUSTOMER, origin="https://x")
    assert len(seen[-1]["metadata"]["items"]) <= 500


async def test_checkout_refuses_a_country_we_do_not_deliver_to(db_session, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "shop_ship_countries", ["AE"])
    far = CUSTOMER.model_copy(update={"country": "US"})
    with pytest.raises(AppError) as err:
        await service.create_checkout(db_session, items=[CartItem(slug="l801", qty=1)], customer=far, origin="https://x")
    assert err.value.code == "SHIP_COUNTRY"


async def test_the_buyer_pays_in_their_currency_and_delivery_is_priced_by_destination(db_session, monkeypatch) -> None:
    """An Indian in Dubai sends a pair home: rupees, plus India delivery."""
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "shop_ship_countries", ["*"])
    seen: list[dict] = []

    async def fake(**kw):
        seen.append(kw)
        return {"url": "https://checkout.stripe.test/cs_in"}

    monkeypatch.setattr(stripe_client, "create_order_session", fake)
    home = CUSTOMER.model_copy(update={"country": "IN", "state": "Maharashtra", "city": "Mumbai"})
    out = await service.create_checkout(db_session, items=[CartItem(slug="gs5", qty=1, colour="Black")], customer=home, origin="https://x", currency="INR")
    assert out == {"url": "https://checkout.stripe.test/cs_in", "total": 11999 + 1500, "currency": "INR", "delivery": 1500}
    li = seen[-1]["line_items"]
    assert li[0]["price_data"] == {"currency": "inr", "unit_amount": 1199900, "product_data": {"name": "GS5 MAX — Black"}}
    assert li[1]["price_data"] == {"currency": "inr", "unit_amount": 150000, "product_data": {"name": "Delivery to IN"}}
    assert seen[-1]["metadata"]["currency"] == "INR" and seen[-1]["metadata"]["delivery"] == "1500"
    assert json.loads(seen[-1]["metadata"]["items"])[0]["p"] == 11999

    # anyone else: dollars, plus rest-of-world delivery
    abroad = CUSTOMER.model_copy(update={"country": "GB", "state": None, "city": "London"})
    out = await service.create_checkout(db_session, items=[CartItem(slug="l801", qty=2)], customer=abroad, origin="https://x", currency="USD")
    assert out["currency"] == "USD" and out["delivery"] == 30 and out["total"] == 89 * 2 + 30
    assert seen[-1]["line_items"][0]["price_data"]["unit_amount"] == 8900
    assert seen[-1]["line_items"][1]["price_data"] == {"currency": "usd", "unit_amount": 3000, "product_data": {"name": "Delivery to GB"}}

    # an unknown currency is refused before Stripe is asked
    with pytest.raises(AppError) as err:
        await service.create_checkout(db_session, items=[CartItem(slug="l801", qty=1)], customer=CUSTOMER, origin="https://x", currency="EUR")
    assert err.value.code == "CURRENCY"


def test_money_reads_the_way_each_currency_is_read() -> None:
    assert service.money(45000, "AED") == "AED 450"
    assert service.money(1199900, "INR") == "₹11,999"
    assert service.money(8900, "USD") == "$89.00"


async def test_checkout_refuses_unknown_models_and_colours(db_session, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    with pytest.raises(AppError) as err:
        await service.create_checkout(db_session, items=[CartItem(slug="gs9", qty=1)], customer=CUSTOMER, origin="https://x")
    assert err.value.code == "UNKNOWN_MODEL"
    with pytest.raises(AppError) as err:
        await service.create_checkout(db_session, items=[CartItem(slug="gs5", qty=1, colour="Pink")], customer=CUSTOMER, origin="https://x")
    assert err.value.code == "UNKNOWN_COLOUR"


def test_the_checkout_route_validates_and_answers_503_without_stripe(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "")
    client = TestClient(create_app())
    r = client.post("/api/v1/shop/checkout", json={"items": []})
    assert r.status_code == 422
    cust = CUSTOMER.model_dump()
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "gs5", "qty": 9}], "customer": cust})
    assert r.status_code == 422
    # no customer, a bad email, a phone with no digits: all 422 before Stripe is asked
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "gs5", "qty": 1, "colour": "Red"}]})
    assert r.status_code == 422
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "l801", "qty": 1}], "customer": {**cust, "email": "nope"}})
    assert r.status_code == 422
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "l801", "qty": 1}], "customer": {**cust, "phone": "call me"}})
    assert r.status_code == 422
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "gs5", "qty": 1, "colour": "Red"}], "customer": cust, "currency": "EUR"})
    assert r.status_code == 422, "the currency is one of AED, INR, USD"
    r = client.post("/api/v1/shop/checkout", json={"items": [{"slug": "gs5", "qty": 1, "colour": "Red"}], "customer": cust})
    assert r.status_code == 503 and r.json()["error"]["code"] == "SHOP_NOT_CONFIGURED"


# ---- the order ---------------------------------------------------------------


async def test_a_paid_session_becomes_one_order_and_mails_operator_and_customer(db_session, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "shop_notify_email", "ops@example.com")
    mails: list[dict] = []
    confirmations: list[dict] = []
    monkeypatch.setattr("app.modules.auth.notifications.send_outage_alert", lambda **kw: mails.append(kw))
    monkeypatch.setattr("app.modules.auth.notifications.send_order_confirmation", lambda **kw: confirmations.append(kw))
    first = await service.record_order(db_session, _session())
    assert first["duplicate"] is False and first["status"] == "paid"
    assert first["amount_cents"] == 105000 and first["currency"] == "AED"
    # what our cart took wins over what Stripe echoes
    assert first["email"] == "buyer@example.com" and first["phone"] == "+971 50 000 0000"
    assert first["address"]["line1"] == "12 Marina Walk" and first["address"]["po_box"] == "12345"
    assert first["name"] == "Ayesha K"
    assert [(i["name"], i["qty"], i["price"]) for i in first["items"]] == [("GS5 MAX", 1, 450), ("L801 Business", 2, 300)]
    assert first["delivery"] == 0
    assert mails and mails[-1]["to_email"] == "ops@example.com"
    assert "1 × GS5 MAX (Red)" in mails[-1]["text"] and "12 Marina Walk" in mails[-1]["text"]
    assert confirmations and confirmations[-1]["to_email"] == "buyer@example.com"
    assert f"order #{first['id']}" in confirmations[-1]["subject"]
    assert "AED 1,050" in confirmations[-1]["text"] and "PO Box 12345" in confirmations[-1]["text"]
    assert "Delivery to AE — AED 0" in confirmations[-1]["text"]

    again = await service.record_order(db_session, _session())
    assert again["duplicate"] is True and again["id"] == first["id"]
    assert len(mails) == 1 and len(confirmations) == 1, "a redelivery must not mail twice"

    # a session that did not come through our cart still records, from Stripe's details
    other = await service.record_order(db_session, _session("cs_theirs", ours=False))
    assert other["email"] == "buyer@example.com" and other["address"]["line1"] == "12 Marina Walk"
    rows = (await db_session.execute(select(Order))).scalars().all()
    assert len(rows) == 2, "two sessions, two orders — the redelivery added none"


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


# ---- back on the site: the order modal -----------------------------------------------


def test_the_order_summary_comes_from_our_row_first_then_stripe(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    calls: list[str] = []

    async def fake(**kw):
        calls.append(kw["session_id"])
        return _session(kw["session_id"])

    monkeypatch.setattr(stripe_client, "retrieve_checkout_session", fake)
    client = TestClient(create_app())

    # not recorded yet (webhook still on its way): Stripe answers, no order number
    r = client.get("/api/v1/shop/orders/cs_pending")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["paid"] is True and d["order_id"] is None and d["amount_cents"] == 105000
    assert d["items"][0]["name"] == "GS5 MAX" and d["address_line"].startswith("12 Marina Walk")
    assert calls == ["cs_pending"]

    # recorded: our row answers, with the number, and Stripe is not asked
    async def seed() -> None:
        async with db_base.get_sessionmaker()() as db:
            await service.record_order(db, _session("cs_done"))

    asyncio.run(seed())
    r = client.get("/api/v1/shop/orders/cs_done")
    assert r.status_code == 200 and r.json()["data"]["order_id"] >= 1 and r.json()["data"]["email"] == "buyer@example.com"
    assert calls == ["cs_pending"]

    # junk is a 404, never a Stripe call
    assert client.get("/api/v1/shop/orders/nope").status_code == 404
    assert calls == ["cs_pending"]


def test_old_success_links_land_back_on_the_site() -> None:
    client = TestClient(create_app())
    r = client.get("/shop/success?session_id=cs_abc", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/?order=cs_abc#glasses"
    r = client.get("/shop/success?session_id=<script>", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/#glasses"


def test_the_page_carries_the_delivery_form_and_the_order_modal() -> None:
    from pathlib import Path

    import app.web.router as web_router

    page = shop.render(Path(web_router._INDEX).read_text(encoding="utf-8"), get_settings())
    for name in ("name", "email", "phone", "line1", "line2", "po_box", "city", "state", "country"):
        assert f'name="{name}"' in page, name
    assert "data-order-modal" in page and "function orderShow" in page and "/api/v1/shop/orders/" in page
    assert "Continue to delivery" in page and "Pay securely" in page
