"""Routes: the public checkout + thank-you page, and the admin order list.

``POST /api/v1/shop/checkout`` needs no account — a visitor buys glasses
from the landing page. Stripe collects the address and phone; nothing
personal passes through here except what Stripe echoes back on the webhook
(modules/billing/router.py hands ``kind=glasses_order`` sessions to
:func:`service.record_order`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_permission
from app.core.responses import AppError, ok
from app.db.models import User
from app.modules.audit.service import write_audit
from app.modules.shop import service
from app.modules.shop.schemas import (
    OrderStatusRequest,
    ShopCheckoutRequest,
    StockRequest,
)

router = APIRouter(prefix="/shop", tags=["shop"])
page_router = APIRouter(tags=["shop"])
admin_router = APIRouter(prefix="/admin", tags=["shop"])


def _origin(request: Request) -> str:
    """The origin the visitor used — https on the public site, http only on a
    developer's localhost (the proxy legs say http; see web/router.py)."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "farryon.izylrn.com"
    local = host.split(":")[0] in ("localhost", "127.0.0.1") or host.startswith("192.168.")
    return f"{'http' if local else 'https'}://{host}"


@router.post("/checkout")
async def shop_checkout_endpoint(
    body: ShopCheckoutRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    return ok(
        await service.create_checkout(
            db, items=body.items, customer=body.customer, origin=_origin(request), currency=body.currency
        )
    )


@router.get("/orders/{session_id}")
async def shop_order_summary_endpoint(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """The order behind a Checkout Session id, for the modal the landing
    page shows on return. Unguessable id, no account; a bad id is a 404."""
    summary = await service.order_summary(db, session_id)
    if summary is None:
        raise AppError("NOT_FOUND", "No such order.", status_code=404)
    return ok(summary)


@page_router.get("/shop/success", include_in_schema=False)
async def shop_success_page(session_id: str = "") -> RedirectResponse:
    """Older success links: back to the landing page, which shows the order
    in its own modal (the site's CSP allows no styling on a page of ours)."""
    safe = session_id if session_id.startswith("cs_") and len(session_id) <= 128 else ""
    return RedirectResponse(url=f"/?order={safe}#glasses" if safe else "/#glasses", status_code=302)


@admin_router.get("/orders", dependencies=[Depends(require_permission("billing.read"))])
async def list_orders_endpoint(
    status: str | None = None,
    page: int = 1,
    page_size: int = service.PAGE_SIZE_DEFAULT,
    db: AsyncSession = Depends(get_db),
) -> dict:
    items, total = await service.list_orders(
        db, status_filter=status, page=page, page_size=page_size
    )
    return ok(items, meta={"page": page, "page_size": page_size, "total": total})


@admin_router.patch("/orders/{order_id}", dependencies=[Depends(require_permission("billing.manage"))])
async def set_order_status_endpoint(
    order_id: int,
    body: OrderStatusRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    out = await service.set_status(db, order_id, status=body.status, note=body.note)
    await write_audit(
        db,
        actor_id=actor.id,
        action="order.status",
        entity_type="order",
        entity_id=order_id,
        after={"status": body.status},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return ok(out)


# ---- Stock (admin) --------------------------------------------------------------


@admin_router.get("/stock", dependencies=[Depends(require_permission("billing.read"))])
async def list_stock_endpoint(db: AsyncSession = Depends(get_db)) -> dict:
    """Every model and colour, and whether it is on sale."""
    return ok(await service.stock_list(db))


@admin_router.put("/stock/{key}", dependencies=[Depends(require_permission("billing.manage"))])
async def set_stock_endpoint(
    key: str,
    body: StockRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Toggle a model (``l802``) or a colour (``gs5:Red``) on or off sale."""
    out = await service.set_stock(db, key, in_stock=body.in_stock)
    await write_audit(
        db,
        actor_id=actor.id,
        action="stock.set",
        entity_type="stock",
        entity_id=None,
        after={"key": key, "in_stock": body.in_stock},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return ok(out)
