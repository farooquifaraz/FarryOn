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
from app.db.models import Order, ShopStock
from app.logging_conf import get_logger
from app.modules.shop.schemas import CartItem
from app.web.products import COLOURS, MODELS, PRICES_AED

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


def _line(item: CartItem, sold_out: set[str]) -> dict:
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
            "currency": "aed",
            "unit_amount": PRICES_AED[item.slug] * 100,
            "product_data": {"name": name},
        },
    }


def _summary(items: list[CartItem]) -> list[dict]:
    """The order's own record of what was bought — in metadata and the row."""
    return [
        {
            "slug": i.slug,
            "name": MODELS[i.slug],
            "colour": (i.colour or None) if COLOURS.get(i.slug) else None,
            "qty": i.qty,
            "price_aed": PRICES_AED[i.slug],
        }
        for i in items
    ]


async def create_checkout(db: AsyncSession, *, items: list[CartItem], origin: str) -> dict:
    """Start a Stripe Checkout for the cart; returns ``{"url": …}``.

    ``origin`` is where the visitor is (https://farryon.izylrn.com, or the
    developer's localhost) — Stripe sends them back there.
    """
    from app.config import get_settings
    from app.services import stripe_client
    from app.services.stripe_client import StripeError

    settings = get_settings()
    if not settings.stripe_secret_key:
        raise AppError("SHOP_NOT_CONFIGURED", "The shop isn't taking orders yet.", status_code=503)
    sold_out = await sold_out_keys(db)
    lines = [_line(i, sold_out) for i in items]
    summary = _summary(items)
    total = sum(i["price_aed"] * i["qty"] for i in summary)
    try:
        session = await stripe_client.create_order_session(
            secret_key=settings.stripe_secret_key,
            line_items=lines,
            success_url=f"{origin}/shop/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/#glasses",
            ship_to=list(settings.shop_ship_countries),
            metadata={"kind": ORDER_KIND, "items": json.dumps(summary, ensure_ascii=False)},
        )
    except StripeError as e:
        raise AppError("STRIPE_ERROR", str(e), status_code=502) from e
    logger.info("shop.checkout_started", items=len(lines), total_aed=total)
    return {"url": session["url"], "total_aed": total}


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
    try:
        items = json.loads(meta.get("items") or "[]")
    except ValueError:
        items = []
    details = session_obj.get("customer_details") or {}
    address = _address(session_obj)
    order = Order(
        session_id=session_id,
        payment_intent=str(session_obj.get("payment_intent") or "") or None,
        email=details.get("email"),
        name=address.get("name") or details.get("name"),
        phone=details.get("phone"),
        address_json=json.dumps(address, ensure_ascii=False),
        items_json=json.dumps(items, ensure_ascii=False),
        amount_cents=int(session_obj.get("amount_total") or 0),
        currency=str(session_obj.get("currency") or "aed").upper(),
        status="paid",
    )
    db.add(order)
    await db.flush()
    await db.commit()
    logger.info("shop.order_recorded", order_id=order.id, amount_cents=order.amount_cents)
    _notify(order, items, address)
    return {**_order_out(order), "duplicate": False}


def _notify(order: Order, items: list[dict], address: dict) -> None:
    """Mail the operator: a new order needs a parcel. Log-only without SMTP."""
    from app.config import get_settings
    from app.modules.auth.notifications import send_outage_alert

    s = get_settings()
    to = s.shop_notify_email or s.first_super_admin_email
    if not to:
        logger.info("shop.order_notify_skipped", reason="no address configured")
        return
    lines = [
        f"Order #{order.id} — {order.currency} {order.amount_cents / 100:.2f}",
        "",
        "Items:",
        *[
            f"  {i.get('qty')} × {i.get('name')}" + (f" ({i['colour']})" if i.get("colour") else "")
            for i in items
        ],
        "",
        f"Customer: {order.name or '-'} · {order.email or '-'} · {order.phone or '-'}",
        "Ship to: "
        + ", ".join(
            str(address.get(k)) for k in ("line1", "line2", "city", "state", "postal_code", "country") if address.get(k)
        ),
        "",
        f"Stripe session: {order.session_id}",
        "Mark it shipped in the admin panel → Orders.",
    ]
    send_outage_alert(to_email=to, subject=f"FarryOn order #{order.id}", text="\n".join(lines))


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


async def set_status(db: AsyncSession, order_id: int, *, status: str, note: str | None) -> dict:
    if status not in STATUSES:
        raise AppError("INVALID_STATUS", f"Status must be one of {', '.join(STATUSES)}.", status_code=400)
    order = await db.get(Order, order_id)
    if order is None:
        raise AppError("NOT_FOUND", "Order not found.", status_code=404)
    order.status = status
    if note is not None:
        order.note = note
    order.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _order_out(order)


async def order_summary(session_id: str) -> dict | None:
    """What the thank-you page shows: fetched from Stripe, never trusted from
    the URL. None when Stripe can't be asked (no key, bad id)."""
    from app.config import get_settings
    from app.services import stripe_client
    from app.services.stripe_client import StripeError

    settings = get_settings()
    if not settings.stripe_secret_key or not session_id.startswith("cs_"):
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
        items = json.loads(meta.get("items") or "[]")
    except ValueError:
        items = []
    return {
        "paid": obj.get("payment_status") == "paid",
        "email": (obj.get("customer_details") or {}).get("email"),
        "amount_cents": int(obj.get("amount_total") or 0),
        "currency": str(obj.get("currency") or "aed").upper(),
        "items": items,
    }
