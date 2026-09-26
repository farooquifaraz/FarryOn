"""Cart → Checkout Session → order row → a mail to whoever ships it.

Nothing is written when a checkout starts: an abandoned cart must not leave
an order behind. The webhook's ``checkout.session.completed`` (metadata
``kind=glasses_order``) is the only thing that creates an order, keyed on the
session id so a redelivery finds the row it already made.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import AppError
from app.db.models import Order, ShopPrice, ShopStock
from app.logging_conf import get_logger
from app.modules.shop.mail import (  # noqa: F401 - re-exported for callers
    address_line,
    money,
)
from app.modules.shop.schemas import CartItem, Customer
from app.web.products import (
    COLOURS,
    CURRENCIES,
    DELIVERY,
    MODELS,
    PRICES,
    PRICES_AED,
    PriceBook,
    default_book,
)

logger = get_logger(__name__)

PAGE_SIZE_DEFAULT = 25
ORDER_KIND = "glasses_order"
STATUSES = ("paid", "shipped", "delivered", "cancelled")


# ---- Stock -------------------------------------------------------------------


async def sold_out_keys(db: AsyncSession) -> set[str]:
    """Everything not for sale right now: the admin panel's toggles
    (shop_stock rows with in_stock false) plus the SHOP_OUT_OF_STOCK env list."""
    from app.config import get_settings
    from app.web.shop import out_of_stock

    rows = (await db.execute(select(ShopStock.key).where(ShopStock.in_stock.is_(False)))).scalars().all()
    return out_of_stock(get_settings()) | set(rows)


async def stock_list(db: AsyncSession) -> list[dict]:
    """Every model and colour with whether it is on sale — the admin's table."""
    from app.web.shop import stock_keys

    sold_out = await sold_out_keys(db)
    return [{**k, "in_stock": k["key"] not in sold_out} for k in stock_keys()]


async def set_stock(db: AsyncSession, key: str, *, in_stock: bool) -> dict:
    from app.web.shop import stock_keys

    known = {k["key"]: k for k in stock_keys()}
    if key not in known:
        raise AppError("UNKNOWN_KEY", f"'{key}' isn't a model or colour we sell.", status_code=404)
    row = (await db.execute(select(ShopStock).where(ShopStock.key == key))).scalar_one_or_none()
    if row is None:
        row = ShopStock(key=key, in_stock=in_stock)
        db.add(row)
    else:
        row.in_stock = in_stock
        row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("shop.stock_set", key=key, in_stock=in_stock)
    return {**known[key], "in_stock": in_stock}


# ---- Prices -----------------------------------------------------------------

#: Ceilings that catch a slipped key (a price typed with two extra zeros),
#: in whole units per currency. Delivery may be 0 (free); a model may not.
_MAX_PRICE = {"AED": 100_000, "INR": 2_500_000, "USD": 30_000}
_DELIVERY_PREFIX = "delivery:"


def price_keys() -> list[dict]:
    """Every priced thing, in card order: each model, then each delivery
    destination (a named country first, "*" = everywhere else last)."""
    out = [{"key": slug, "label": MODELS[slug], "kind": "model"} for slug in PRICES]
    for country in DELIVERY:
        label = "Delivery — everywhere else" if country == "*" else f"Delivery — {country}"
        out.append({"key": f"{_DELIVERY_PREFIX}{country}", "label": label, "kind": "delivery"})
    return out


def _default_prices(key: str) -> dict[str, int]:
    if key.startswith(_DELIVERY_PREFIX):
        return dict(DELIVERY[key[len(_DELIVERY_PREFIX):]])
    return dict(PRICES[key])


async def price_book(db: AsyncSession) -> PriceBook:
    """The prices in force: the code's lists with the admin's edits laid
    over them. A row for a key or currency that no longer exists is ignored
    rather than trusted."""
    rows = (await db.execute(select(ShopPrice))).scalars().all()
    book = default_book()
    for r in rows:
        cur = (r.currency or "").upper()
        if cur not in CURRENCIES or r.amount is None:
            continue
        if r.key.startswith(_DELIVERY_PREFIX):
            country = r.key[len(_DELIVERY_PREFIX):]
            if country in book.delivery:
                book.delivery[country][cur] = int(r.amount)
        elif r.key in book.prices:
            book.prices[r.key][cur] = int(r.amount)
    return book


async def price_list(db: AsyncSession) -> list[dict]:
    """The admin's table: each priced thing, what is charged now in each
    currency, the code's default, and which currencies were changed."""
    book = await price_book(db)
    out = []
    for k in price_keys():
        key = k["key"]
        if k["kind"] == "delivery":
            now = book.delivery[key[len(_DELIVERY_PREFIX):]]
        else:
            now = book.prices[key]
        default = _default_prices(key)
        out.append({
            **k,
            "prices": {c: int(now[c]) for c in CURRENCIES},
            "defaults": {c: int(default[c]) for c in CURRENCIES},
            "custom": [c for c in CURRENCIES if int(now[c]) != int(default[c])],
        })
    return out


async def set_price(
    db: AsyncSession, key: str, *, currency: str, amount: int | None
) -> tuple[dict, int | None]:
    """Set one price (``amount``) or put it back to the code's list
    (``None``). Returns the key's row as :func:`price_list` shows it, and
    the amount charged before the change (for the audit log)."""
    known = {k["key"]: k for k in price_keys()}
    if key not in known:
        raise AppError("UNKNOWN_KEY", f"'{key}' isn't a model or delivery we price.", status_code=404)
    currency = currency.upper()
    if currency not in CURRENCIES:
        raise AppError("CURRENCY", f"Use one of {', '.join(CURRENCIES)}.", status_code=400)
    is_delivery = known[key]["kind"] == "delivery"
    if amount is not None:
        floor = 0 if is_delivery else 1
        if amount < floor or amount > _MAX_PRICE[currency]:
            raise AppError(
                "PRICE_RANGE",
                f"{currency} price must be between {floor} and {_MAX_PRICE[currency]:,}.",
                status_code=400,
            )
    before = next(r for r in await price_list(db) if r["key"] == key)["prices"][currency]
    row = (
        await db.execute(
            select(ShopPrice).where(ShopPrice.key == key, ShopPrice.currency == currency)
        )
    ).scalar_one_or_none()
    if amount is None:
        if row is not None:
            await db.delete(row)
    elif row is None:
        db.add(ShopPrice(key=key, currency=currency, amount=int(amount)))
    else:
        row.amount = int(amount)
        row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("shop.price_set", key=key, currency=currency, amount=amount, before=before)
    return next(r for r in await price_list(db) if r["key"] == key), before


def _line(
    item: CartItem, sold_out: set[str], currency: str = "AED", book: PriceBook | None = None
) -> dict:
    """One Stripe line item, priced from the catalog — never from the client.

    Refuses a model or colour that is sold out: the page greys those out,
    but a cart saved in the browser last week does not know, and Stripe
    must never take money for a pair we can't ship.
    """
    from app.web.shop import colour_in_stock, model_in_stock

    if item.slug not in PRICES_AED:
        raise AppError("UNKNOWN_MODEL", f"'{item.slug}' isn't a model we sell.", status_code=400)
    if not model_in_stock(sold_out, item.slug):
        raise AppError("OUT_OF_STOCK", f"{MODELS[item.slug]} is out of stock right now.", status_code=400)
    colours = COLOURS.get(item.slug, [])
    colour = (item.colour or "").strip() or None
    if colours and colour not in colours:
        raise AppError(
            "UNKNOWN_COLOUR",
            f"{MODELS[item.slug]} comes in {', '.join(colours)}.",
            status_code=400,
        )
    if colours and colour and not colour_in_stock(sold_out, item.slug, colour):
        raise AppError(
            "OUT_OF_STOCK", f"{MODELS[item.slug]} in {colour} is out of stock right now.", status_code=400
        )
    if not colours:
        colour = None
    name = MODELS[item.slug] + (f" — {colour}" if colour else "")
    return {
        "quantity": item.qty,
        "price_data": {
            "currency": currency.lower(),
            "unit_amount": (book or default_book()).price(item.slug, currency) * 100,
            "product_data": {"name": name},
        },
    }


def _delivery_line(country: str, currency: str, book: PriceBook | None = None) -> dict | None:
    """The courier, as a line of its own on the Stripe page — or None when
    delivery there is free."""
    amount = (book or default_book()).delivery_charge(country, currency)
    if amount <= 0:
        return None
    return {
        "quantity": 1,
        "price_data": {
            "currency": currency.lower(),
            "unit_amount": amount * 100,
            "product_data": {"name": f"Delivery to {country}"},
        },
    }


def _summary(
    items: list[CartItem], currency: str = "AED", book: PriceBook | None = None
) -> list[dict]:
    """The order's own record of what was bought — in the row and the mails.
    ``price`` is per unit in the order's currency; ``price_aed`` stays for
    older readers."""
    return [
        {
            "slug": i.slug,
            "name": MODELS[i.slug],
            "colour": (i.colour or None) if COLOURS.get(i.slug) else None,
            "qty": i.qty,
            "price": (book or default_book()).price(i.slug, currency),
            "price_aed": (book or default_book()).price(i.slug, "AED"),
        }
        for i in items
    ]


def _compact(summary: list[dict]) -> str:
    """The items as Stripe metadata: a value is capped at 500 characters, so
    ten lines must fit in short keys — ``[{"s":"gs5","c":"Red","q":1,"p":450}]``
    (``p`` = unit price in the order's currency)."""
    return json.dumps(
        [{"s": i["slug"], "c": i["colour"], "q": i["qty"], "p": i["price"]} for i in summary],
        separators=(",", ":"),
    )


def _expand(raw: str | None) -> list[dict]:
    """Back from :func:`_compact` (older long-form metadata still reads)."""
    try:
        rows = json.loads(raw or "[]")
    except ValueError:
        return []
    out = []
    for r in rows:
        slug = r.get("s") or r.get("slug") or ""
        price = int(r.get("p") or r.get("price") or r.get("price_aed") or PRICES_AED.get(slug, 0))
        out.append(
            {
                "slug": slug,
                "name": r.get("name") or MODELS.get(slug, slug),
                "colour": r.get("c") if "c" in r else r.get("colour"),
                "qty": int(r.get("q") or r.get("qty") or 0),
                "price": price,
                "price_aed": price,  # older readers; the order's currency says what it is
            }
        )
    return out


def _address_of(c: Customer) -> dict:
    return {
        "name": c.name,
        "line1": c.line1,
        "line2": c.line2,
        "po_box": c.po_box,
        "city": c.city,
        "state": c.state,
        "postal_code": c.postal_code,
        "country": c.country,
    }




async def create_checkout(
    db: AsyncSession, *, items: list[CartItem], customer: Customer, origin: str, currency: str = "AED"
) -> dict:
    """Start a Stripe Checkout for the cart; returns ``{"url": …}``.

    The buyer's details (asked for in the cart) ride in the session's
    metadata — Stripe only takes the card — and come back on the webhook to
    make the order. ``origin`` is where the visitor is
    (https://farryon.izylrn.com, or the developer's localhost): Stripe sends
    them back to the landing page, which shows the order in a modal.
    """
    from app.config import get_settings
    from app.services import stripe_client
    from app.services.stripe_client import StripeError

    settings = get_settings()
    if not settings.stripe_secret_key:
        raise AppError("SHOP_NOT_CONFIGURED", "The shop isn't taking orders yet.", status_code=503)
    from app.web.shop import ship_countries

    currency = currency.upper()
    if currency not in CURRENCIES:
        raise AppError("CURRENCY", f"Pay in one of {', '.join(CURRENCIES)}.", status_code=400)
    allowed = ship_countries(settings)
    if customer.country not in allowed:
        raise AppError(
            "SHIP_COUNTRY",
            "We don't deliver to that country yet — ask us on WhatsApp.",
            status_code=400,
        )
    sold_out = await sold_out_keys(db)
    # One read of the prices for the whole checkout: the Stripe lines, the
    # delivery line and the order summary can't straddle an admin edit.
    book = await price_book(db)
    lines = [_line(i, sold_out, currency, book) for i in items]
    delivery = book.delivery_charge(customer.country, currency)
    courier = _delivery_line(customer.country, currency, book)
    if courier:
        lines.append(courier)
    summary = _summary(items, currency, book)
    total = sum(i["price"] * i["qty"] for i in summary) + delivery
    metadata = {
        "kind": ORDER_KIND,
        "items": _compact(summary),
        "currency": currency,
        "delivery": str(delivery),
        "customer": json.dumps(
            {"email": customer.email, "name": customer.name, "phone": customer.phone},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "ship": json.dumps(_address_of(customer), ensure_ascii=False, separators=(",", ":")),
    }
    try:
        session = await stripe_client.create_order_session(
            secret_key=settings.stripe_secret_key,
            line_items=lines,
            success_url=f"{origin}/?order={{CHECKOUT_SESSION_ID}}#glasses",
            cancel_url=f"{origin}/?cart=cancelled#glasses",
            metadata=metadata,
            customer_email=customer.email,
        )
    except StripeError as e:
        raise AppError("STRIPE_ERROR", str(e), status_code=502) from e
    logger.info("shop.checkout_started", items=len(items), currency=currency, total=total, delivery=delivery)
    return {"url": session["url"], "total": total, "currency": currency, "delivery": delivery}


def _address(obj: dict) -> dict:
    """The shipping address wherever this API version put it, else billing."""
    ship = (
        (obj.get("collected_information") or {}).get("shipping_details")
        or obj.get("shipping_details")
        or obj.get("shipping")
        or {}
    )
    addr = ship.get("address") or (obj.get("customer_details") or {}).get("address") or {}
    return {
        "name": ship.get("name") or (obj.get("customer_details") or {}).get("name"),
        "line1": addr.get("line1"),
        "line2": addr.get("line2"),
        "city": addr.get("city"),
        "state": addr.get("state"),
        "postal_code": addr.get("postal_code"),
        "country": addr.get("country"),
    }


def _delivery_of(o: Order) -> int:
    """The courier charge inside the order total, in whole units: what was
    paid minus the items (older rows without it come out as 0)."""
    try:
        items = json.loads(o.items_json or "[]")
    except ValueError:
        return 0
    goods = sum(int(i.get("price") or i.get("price_aed") or 0) * int(i.get("qty") or 0) for i in items)
    return max(0, o.amount_cents // 100 - goods)


def _order_out(o: Order) -> dict:
    return {
        "id": o.id,
        "session_id": o.session_id,
        "payment_intent": o.payment_intent,
        "email": o.email,
        "name": o.name,
        "phone": o.phone,
        "address": json.loads(o.address_json) if o.address_json else {},
        "items": json.loads(o.items_json) if o.items_json else [],
        "amount_cents": o.amount_cents,
        "currency": o.currency,
        "delivery": _delivery_of(o),
        "status": o.status,
        "note": o.note,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }


async def record_order(db: AsyncSession, session_obj: dict) -> dict:
    """Turn a completed Checkout Session into an order row (idempotent).

    Returns the order dict plus ``duplicate`` — True when the session had
    already been recorded (Stripe redelivers), in which case nothing is
    written or mailed again.
    """
    session_id = str(session_obj.get("id") or "")
    if not session_id:
        raise AppError("INVALID_EVENT", "Checkout session without an id.", status_code=400)
    existing = (
        await db.execute(select(Order).where(Order.session_id == session_id))
    ).scalar_one_or_none()
    if existing:
        return {**_order_out(existing), "duplicate": True}

    meta = session_obj.get("metadata") or {}
    items = _expand(meta.get("items"))
    details = session_obj.get("customer_details") or {}
    # Our cart's details first (metadata); Stripe's own only when a session
    # came from somewhere else (an old link, a Payment Link).
    try:
        ours = json.loads(meta.get("customer") or "{}")
        ship = json.loads(meta.get("ship") or "{}")
    except ValueError:
        ours, ship = {}, {}
    address = ship if ship.get("line1") else _address(session_obj)
    order = Order(
        session_id=session_id,
        payment_intent=str(session_obj.get("payment_intent") or "") or None,
        email=ours.get("email") or details.get("email"),
        name=ours.get("name") or address.get("name") or details.get("name"),
        phone=ours.get("phone") or details.get("phone"),
        address_json=json.dumps(address, ensure_ascii=False),
        items_json=json.dumps(items, ensure_ascii=False),
        amount_cents=int(session_obj.get("amount_total") or 0),
        currency=str(session_obj.get("currency") or meta.get("currency") or "aed").upper(),
        status="paid",
    )
    db.add(order)
    await db.flush()
    await db.commit()
    logger.info("shop.order_recorded", order_id=order.id, amount_cents=order.amount_cents)
    _notify(order, items, address)
    _confirm(order, items, address)
    return {**_order_out(order), "duplicate": False}


def _confirm(order: Order, items: list[dict], address: dict) -> None:
    """The customer's confirmation, in the FarryOn look (modules/shop/mail).
    Log-only without SMTP."""
    from app.modules.auth.notifications import send_order_confirmation
    from app.modules.shop import mail

    if not order.email:
        return
    subject, text, html = mail.customer_confirmation(order, items, address, _delivery_of(order) * 100)
    send_order_confirmation(to_email=order.email, subject=subject, text=text, html=html)


def _notify(order: Order, items: list[dict], address: dict) -> None:
    """Mail the operator: a new order needs a parcel. Log-only without SMTP."""
    from app.config import get_settings
    from app.modules.auth.notifications import send_operator_mail
    from app.modules.shop import mail

    s = get_settings()
    to = s.shop_notify_email or s.first_super_admin_email
    if not to:
        logger.info("shop.order_notify_skipped", reason="no address configured")
        return
    subject, text, html = mail.operator_alert(order, items, address, _delivery_of(order) * 100)
    send_operator_mail(to_email=to, subject=subject, text=text, html=html)


async def list_orders(
    db: AsyncSession, *, status_filter: str | None, page: int, page_size: int
) -> tuple[list[dict], int]:
    q = select(Order)
    if status_filter:
        q = q.where(Order.status == status_filter)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (
        await db.execute(
            q.order_by(Order.created_at.desc(), Order.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [_order_out(o) for o in rows], int(total)


async def set_status(
    db: AsyncSession, order_id: int, *, status: str, note: str | None, by: str | None = None
) -> dict:
    """Move an order along and tell both sides — the customer gets the
    status mail (shipped / delivered / cancelled), the operator the record.
    The same status again sends nothing (a double click is not two mails)."""
    if status not in STATUSES:
        raise AppError("INVALID_STATUS", f"Status must be one of {', '.join(STATUSES)}.", status_code=400)
    order = await db.get(Order, order_id)
    if order is None:
        raise AppError("NOT_FOUND", "Order not found.", status_code=404)
    changed = order.status != status
    order.status = status
    if note is not None:
        order.note = note
    order.updated_at = datetime.now(timezone.utc)
    await db.commit()
    if changed:
        try:
            items = json.loads(order.items_json or "[]")
            address = json.loads(order.address_json or "{}")
        except ValueError:
            items, address = [], {}
        _status_mails(order, items, address, note, by)
    return _order_out(order)


def _status_mails(order: Order, items: list[dict], address: dict, note: str | None, by: str | None) -> None:
    from app.config import get_settings
    from app.modules.auth.notifications import (
        send_operator_mail,
        send_order_confirmation,
    )
    from app.modules.shop import mail

    delivery = _delivery_of(order) * 100
    if order.email:
        subject, text, html = mail.customer_status(order, items, address, delivery, note)
        send_order_confirmation(to_email=order.email, subject=subject, text=text, html=html)
    s = get_settings()
    to = s.shop_notify_email or s.first_super_admin_email
    if to:
        subject, text, html = mail.operator_status(order, items, address, delivery, note, by)
        send_operator_mail(to_email=to, subject=subject, text=text, html=html)


async def order_summary(db: AsyncSession, session_id: str) -> dict | None:
    """What the order modal shows after Stripe sends the buyer back.

    The order row first (the webhook usually lands before the redirect, and
    the row has the order number); Stripe itself when it hasn't yet. Never
    trusted from the URL: a made-up id gives None.
    """
    from app.config import get_settings
    from app.services import stripe_client
    from app.services.stripe_client import StripeError

    if not session_id.startswith("cs_") or len(session_id) > 128:
        return None
    row = (await db.execute(select(Order).where(Order.session_id == session_id))).scalar_one_or_none()
    if row:
        out = _order_out(row)
        return {
            "paid": True,
            "order_id": out["id"],
            "email": out["email"],
            "name": out["name"],
            "amount_cents": out["amount_cents"],
            "currency": out["currency"],
            "delivery": out["delivery"],
            "items": out["items"],
            "address": out["address"],
            "address_line": address_line(out["address"]),
        }
    settings = get_settings()
    if not settings.stripe_secret_key:
        return None
    try:
        obj = await stripe_client.retrieve_checkout_session(
            secret_key=settings.stripe_secret_key, session_id=session_id
        )
    except StripeError:
        return None
    meta = obj.get("metadata") or {}
    if meta.get("kind") != ORDER_KIND:
        return None
    try:
        ours = json.loads(meta.get("customer") or "{}")
        ship = json.loads(meta.get("ship") or "{}")
    except ValueError:
        ours, ship = {}, {}
    address = ship if ship.get("line1") else _address(obj)
    try:
        delivery = int(meta.get("delivery") or 0)
    except ValueError:
        delivery = 0
    return {
        "paid": obj.get("payment_status") == "paid",
        "order_id": None,
        "email": ours.get("email") or (obj.get("customer_details") or {}).get("email"),
        "name": ours.get("name") or address.get("name"),
        "amount_cents": int(obj.get("amount_total") or 0),
        "currency": str(obj.get("currency") or meta.get("currency") or "aed").upper(),
        "delivery": delivery,
        "items": _expand(meta.get("items")),
        "address": address,
        "address_line": address_line(address),
    }


async def revenue_summary(db: AsyncSession) -> dict:
    """Glasses money for the dashboard: totals per currency (never summed
    across currencies — AED, INR and USD are three piles), counts by
    status, and the last twelve months per currency."""
    rows = (await db.execute(select(Order))).scalars().all()
    by_cur: dict[str, dict] = {}
    by_status: dict[str, int] = {}
    by_month: dict[str, dict[str, int]] = {}
    for o in rows:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        if o.status == "cancelled":
            continue
        c = by_cur.setdefault(o.currency, {"currency": o.currency, "orders": 0, "amount_cents": 0, "delivery_cents": 0, "units": 0})
        c["orders"] += 1
        c["amount_cents"] += o.amount_cents
        c["delivery_cents"] += _delivery_of(o) * 100
        try:
            c["units"] += sum(int(i.get("qty") or 0) for i in json.loads(o.items_json or "[]"))
        except ValueError:
            pass
        m = o.created_at.strftime("%Y-%m") if o.created_at else "?"
        by_month.setdefault(m, {})
        by_month[m][o.currency] = by_month[m].get(o.currency, 0) + o.amount_cents
    return {
        "orders_total": len(rows),
        "by_status": by_status,
        "by_currency": sorted(by_cur.values(), key=lambda x: x["currency"]),
        "over_time": [{"month": m, "amounts": a} for m, a in sorted(by_month.items())][-12:],
    }
