"""Glasses and delivery prices edited from the admin panel.

Faraz, 2026-09-26: "agr koi glasses ki price change kerni h … to kese karu" —
prices lived only in ``app.web.products`` and a change meant a code push.
Pinned here: an admin edit reaches the spec cards, the cart catalog and the
Stripe charge alike (the figure seen is the figure paid), a reset goes back
to the code's list, a slipped key is refused, orders already made keep their
price, and only billing managers may edit — with an audit row per change.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.core.responses import AppError
from app.db import base as db_base
from app.db.models import AuditLog
from app.main import create_app
from app.modules.shop import service
from app.modules.shop.schemas import CartItem, Customer
from app.services import stripe_client
from app.web import products, shop
from tests.test_shop import _auth, _login, _register_user, _setup_admin

CUSTOMER = Customer(
    email="buyer@example.com", name="Ayesha K", phone="+971 50 000 0000",
    line1="12 Marina Walk", city="Dubai", state="Dubai", country="ae",
)


# ---- the service -----------------------------------------------------------------


async def test_no_edit_means_the_codes_prices(db_session) -> None:
    book = await service.price_book(db_session)
    assert book.prices == products.PRICES
    assert book.delivery == products.DELIVERY
    rows = await service.price_list(db_session)
    assert [r["key"] for r in rows] == [
        "l801", "l802", "gs4", "gs5", "delivery:AE", "delivery:IN", "delivery:*",
    ]
    assert all(r["custom"] == [] for r in rows)


async def test_an_edit_is_laid_over_the_list_and_a_reset_removes_it(db_session) -> None:
    row, before = await service.set_price(db_session, "gs5", currency="AED", amount=499)
    assert before == 450
    assert row["prices"]["AED"] == 499 and row["defaults"]["AED"] == 450
    assert row["custom"] == ["AED"]
    book = await service.price_book(db_session)
    assert book.price("gs5", "AED") == 499
    assert book.price("gs5", "INR") == products.PRICES["gs5"]["INR"], "only that currency moved"
    assert products.PRICES["gs5"]["AED"] == 450, "the code's list is never mutated"

    row, before = await service.set_price(db_session, "gs5", currency="AED", amount=None)
    assert before == 499 and row["prices"]["AED"] == 450 and row["custom"] == []


async def test_delivery_can_be_made_free_or_priced(db_session) -> None:
    await service.set_price(db_session, "delivery:IN", currency="INR", amount=0)
    await service.set_price(db_session, "delivery:*", currency="USD", amount=45)
    book = await service.price_book(db_session)
    assert book.delivery_charge("IN", "INR") == 0
    assert book.delivery_charge("GB", "USD") == 45, "'*' covers everywhere else"
    assert book.delivery_charge("AE", "AED") == 0


@pytest.mark.parametrize(
    "key, currency, amount, code",
    [
        ("gs9", "AED", 400, "UNKNOWN_KEY"),
        ("gs5", "EUR", 400, "CURRENCY"),
        ("gs5", "AED", 0, "PRICE_RANGE"),          # a pair is never free
        ("gs5", "AED", 450_000, "PRICE_RANGE"),    # two extra zeros
        ("delivery:IN", "INR", -5, "PRICE_RANGE"),
    ],
)
async def test_a_slipped_key_is_refused(db_session, key, currency, amount, code) -> None:
    with pytest.raises(AppError) as err:
        await service.set_price(db_session, key, currency=currency, amount=amount)
    assert err.value.code == code
    assert (await service.price_book(db_session)).prices == products.PRICES


# ---- everything that shows or charges a price follows ------------------------------


async def test_the_page_the_cart_and_stripe_all_see_the_edit(db_session, monkeypatch) -> None:
    await service.set_price(db_session, "gs5", currency="AED", amount=499)
    await service.set_price(db_session, "gs5", currency="INR", amount=12999)
    await service.set_price(db_session, "delivery:IN", currency="INR", amount=999)
    book = await service.price_book(db_session)

    html = shop.render(
        "<!--PRICE_AED:gs5--> <!--PRICE_FIXED:gs5--> <!--SHOP_CATALOG-->",
        get_settings(), set(), book,
    )
    assert html.startswith('499 data-inr="12999" data-usd="129"')
    cat = shop.catalog(get_settings(), set(), book)
    assert cat["items"]["gs5"]["price_aed"] == 499
    assert cat["items"]["gs5"]["prices"] == {"AED": 499, "INR": 12999, "USD": 129}
    assert cat["delivery"]["IN"]["INR"] == 999

    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "shop_ship_countries", ["*"])
    seen: list[dict] = []

    async def fake(**kw):
        seen.append(kw)
        return {"url": "https://checkout.stripe.test/cs_p"}

    monkeypatch.setattr(stripe_client, "create_order_session", fake)
    out = await service.create_checkout(
        db_session, items=[CartItem(slug="gs5", qty=2, colour="Red")],
        customer=CUSTOMER, origin="https://x",
    )
    assert out["total"] == 998 and seen[-1]["line_items"][0]["price_data"]["unit_amount"] == 49900
    assert json.loads(seen[-1]["metadata"]["items"])[0]["p"] == 499

    home = CUSTOMER.model_copy(update={"country": "IN", "state": "Maharashtra", "city": "Mumbai"})
    out = await service.create_checkout(
        db_session, items=[CartItem(slug="gs5", qty=1, colour="Black")],
        customer=home, origin="https://x", currency="INR",
    )
    assert out == {"url": "https://checkout.stripe.test/cs_p", "total": 12999 + 999, "currency": "INR", "delivery": 999}
    li = seen[-1]["line_items"]
    assert li[0]["price_data"]["unit_amount"] == 1299900
    assert li[1]["price_data"]["unit_amount"] == 99900


def test_the_admin_edits_a_price_and_the_landing_page_follows() -> None:
    client = TestClient(create_app())
    admin = _setup_admin(client, "prices-admin@example.com")
    _register_user(client, "prices-plain@example.com")
    plain = _login(client, "prices-plain@example.com")

    r = client.get("/api/v1/admin/prices", headers=_auth(admin))
    assert r.status_code == 200
    gs5 = next(row for row in r.json()["data"] if row["key"] == "gs5")
    assert gs5["prices"] == {"AED": 450, "INR": 11999, "USD": 129}

    # an ordinary user can neither read nor change prices
    assert client.get("/api/v1/admin/prices", headers=_auth(plain)).status_code == 403
    r = client.put("/api/v1/admin/prices/gs5", headers=_auth(plain), json={"currency": "AED", "amount": 1})
    assert r.status_code == 403

    r = client.put("/api/v1/admin/prices/gs5", headers=_auth(admin), json={"currency": "AED", "amount": 475})
    assert r.status_code == 200 and r.json()["data"]["prices"]["AED"] == 475
    page = client.get("/").text
    assert '"price_aed": 475' in page
    # every GS5 price on the page moved — none still says the old figure
    # (device 2026-09-26: a hard-coded strip in the pricing section did)
    assert 'data-aed="475"' in page and 'data-aed="450"' not in page

    r = client.put("/api/v1/admin/prices/gs5", headers=_auth(admin), json={"currency": "AED", "amount": 0})
    assert r.status_code == 400 and r.json()["error"]["code"] == "PRICE_RANGE"
    r = client.put("/api/v1/admin/prices/gs5", headers=_auth(admin), json={"currency": "EUR", "amount": 5})
    assert r.status_code == 422

    # who changed what, from what to what
    async def audit() -> list[AuditLog]:
        async with db_base.get_sessionmaker()() as db:
            return list((await db.execute(select(AuditLog).where(AuditLog.action == "price.set"))).scalars())

    rows = asyncio.run(audit())
    assert len(rows) == 1
    assert json.loads(rows[0].before_json) == {"key": "gs5", "currency": "AED", "amount": 450}
    assert json.loads(rows[0].after_json) == {"key": "gs5", "currency": "AED", "amount": 475}

    # back to the standard price
    r = client.put("/api/v1/admin/prices/gs5", headers=_auth(admin), json={"currency": "AED", "amount": None})
    assert r.status_code == 200 and r.json()["data"]["prices"]["AED"] == 450
    assert '"price_aed": 450' in client.get("/").text
