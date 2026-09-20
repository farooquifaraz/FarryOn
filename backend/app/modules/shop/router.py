"""Routes: the public checkout + thank-you page, and the admin order list.

``POST /api/v1/shop/checkout`` needs no account — a visitor buys glasses
from the landing page. Stripe collects the address and phone; nothing
personal passes through here except what Stripe echoes back on the webhook
(modules/billing/router.py hands ``kind=glasses_order`` sessions to
:func:`service.record_order`).
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_permission
from app.core.responses import ok
from app.db.models import User
from app.modules.audit.service import write_audit
from app.modules.shop import service
from app.modules.shop.schemas import OrderStatusRequest, ShopCheckoutRequest, StockRequest

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
    return ok(await service.create_checkout(db, items=body.items, origin=_origin(request)))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — FarryOn</title>
<style>
  body {{ background:#030912; color:#e7ecf3; font-family:Inter,system-ui,sans-serif; margin:0;
         min-height:100vh; display:flex; align-items:center; justify-content:center; text-align:center; }}
  .card {{ padding:36px 28px; max-width:520px; }}
  h1 {{ font-size:1.5rem; margin:0 0 10px; color:#7fe3c8; }}
  p {{ color:#93a1b5; line-height:1.6; }}
  ul {{ list-style:none; padding:0; margin:18px 0; color:#cfe6e0; }}
  li {{ padding:4px 0; }}
  a {{ display:inline-block; margin-top:22px; padding:12px 26px; border-radius:50px;
       background:linear-gradient(135deg,#0F6E56,#00D4AA); color:#030912; font-weight:700; text-decoration:none; }}
</style></head>
<body><div class="card"><h1>{title}</h1>{body}<a href="/">Back to FarryOn</a></div></body></html>"""


@page_router.get("/shop/success", include_in_schema=False)
async def shop_success_page(session_id: str = "") -> HTMLResponse:
    """Where Stripe sends the browser after paying. Reads the session from
    Stripe (never from the URL) to say what was bought; the order itself is
    written by the webhook, not here."""
    summary = await service.order_summary(session_id)
    if summary and summary["paid"]:
        items = "".join(
            f"<li>{i.get('qty')} × {escape(str(i.get('name')))}"
            + (f" ({escape(str(i['colour']))})" if i.get("colour") else "")
            + "</li>"
            for i in summary["items"]
        )
        body = (
            f"<p>Thank you — we have your order.</p><ul>{items}</ul>"
            f"<p><b>{summary['currency']} {summary['amount_cents'] / 100:.0f}</b> paid"
            + (f" · a receipt is on its way to {escape(summary['email'])}" if summary["email"] else "")
            + ".</p><p>We will message you when your glasses ship.</p>"
        )
        return HTMLResponse(_PAGE.format(title="Order received \N{PARTY POPPER}", body=body))
    return HTMLResponse(
        _PAGE.format(
            title="Thank you",
            body="<p>If your payment went through you will get a receipt from Stripe by email, "
            "and we will message you when your glasses ship.</p>",
        )
    )


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
