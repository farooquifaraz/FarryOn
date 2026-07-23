"""``/api/v1/admin/plans``, ``/admin/subscriptions``, ``/admin/revenue/*``
(billing.read / billing.manage) and ``POST /api/v1/webhooks/billing/{provider}``.

The webhook route is unauthenticated by nature (the provider calls it) but
gated by a shared secret header until a real provider's HMAC signature
scheme replaces it — see Settings.billing_webhook_secret.
"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.deps import get_current_user, get_db, require_permission
from app.core.responses import AppError, ok
from app.db.models import User
from app.modules.audit.service import write_audit
from app.modules.billing import service, stripe_webhook
from app.modules.billing.schemas import (
    CheckoutRequest,
    PlanCreateRequest,
    PlanUpdateRequest,
    WebhookEvent,
)

router = APIRouter(prefix="/admin", tags=["billing"])
webhook_router = APIRouter(prefix="/webhooks", tags=["billing"])
# User-facing (not admin): the signed-in user starting their own subscription.
me_router = APIRouter(prefix="/billing", tags=["billing"])


@me_router.get("/me")
async def subscription_overview_endpoint(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The caller's plan, today's usage against its caps, and what they could
    upgrade to. Backs the app's Settings → Subscription screen."""
    return ok(await service.subscription_overview(db, user=user))


@me_router.post("/checkout")
async def create_checkout_endpoint(
    body: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Start a Stripe Checkout Session for the caller. Returns ``{url}`` to
    redirect to. Authenticated as the user themselves — no admin permission;
    anyone signed in may subscribe."""
    return ok(await service.create_checkout(db, user=user, plan_name=body.plan))


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---- Plans -----------------------------------------------------------------


@router.get("/plans", dependencies=[Depends(require_permission("billing.read"))])
async def list_plans_endpoint(db: AsyncSession = Depends(get_db)) -> dict:
    return ok(await service.list_plans(db))


@router.post("/plans", dependencies=[Depends(require_permission("billing.manage"))])
async def create_plan_endpoint(
    body: PlanCreateRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    plan = await service.create_plan(
        db,
        name=body.name,
        price_cents=body.price_cents,
        currency=body.currency,
        interval=body.interval,
        description=body.description,
        features=body.features,
    )
    await write_audit(
        db,
        actor_id=actor.id,
        action="plan.create",
        entity_type="plan",
        entity_id=plan["id"],
        after=body.model_dump(),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(plan)


@router.patch("/plans/{plan_id}", dependencies=[Depends(require_permission("billing.manage"))])
async def update_plan_endpoint(
    plan_id: int,
    body: PlanUpdateRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    plan = await service.update_plan(
        db,
        plan_id,
        price_cents=body.price_cents,
        description=body.description,
        features=body.features,
        is_active=body.is_active,
    )
    await write_audit(
        db,
        actor_id=actor.id,
        action="plan.update",
        entity_type="plan",
        entity_id=plan_id,
        after=body.model_dump(exclude_none=True),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(plan)


# ---- Subscriptions & revenue --------------------------------------------------


@router.get("/subscriptions", dependencies=[Depends(require_permission("billing.read"))])
async def list_subscriptions_endpoint(
    status: str | None = None,
    plan: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = service.PAGE_SIZE_DEFAULT,
    db: AsyncSession = Depends(get_db),
) -> dict:
    items, total = await service.list_subscriptions(
        db,
        status_filter=status,
        plan_filter=plan,
        search=search,
        page=page,
        page_size=page_size,
    )
    return ok(items, meta={"page": page, "page_size": page_size, "total": total})


@router.get("/revenue/summary", dependencies=[Depends(require_permission("billing.read"))])
async def revenue_summary_endpoint(db: AsyncSession = Depends(get_db)) -> dict:
    return ok(await service.revenue_summary(db))


@router.get(
    "/revenue/transactions", dependencies=[Depends(require_permission("billing.read"))]
)
async def list_transactions_endpoint(
    status: str | None = None,
    page: int = 1,
    page_size: int = service.PAGE_SIZE_DEFAULT,
    db: AsyncSession = Depends(get_db),
) -> dict:
    items, total = await service.list_transactions(
        db, status_filter=status, page=page, page_size=page_size
    )
    return ok(items, meta={"page": page, "page_size": page_size, "total": total})


# ---- Webhook -------------------------------------------------------------------


@webhook_router.post("/billing/{provider}")
async def billing_webhook_endpoint(
    provider: str,
    event: WebhookEvent,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.billing_webhook_secret:
        raise AppError(
            "WEBHOOK_NOT_CONFIGURED", "Billing webhooks aren't configured.", status_code=503
        )
    if not x_webhook_secret or not hmac.compare_digest(
        x_webhook_secret, settings.billing_webhook_secret
    ):
        raise AppError("UNAUTHENTICATED", "Invalid webhook secret.", status_code=401)

    result = await service.handle_webhook_event(db, provider=provider, event=event)
    await write_audit(
        db,
        actor_id=None,
        action=f"billing.{event.event_type}",
        entity_type="billing",
        entity_id=result.get("subscription_id") or result.get("payment_id"),
        after=event.model_dump(mode="json"),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(result)


@webhook_router.post("/stripe")
async def stripe_webhook_endpoint(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Receive a Stripe webhook: verify its signature, translate it, apply it.

    Reads the RAW body — Stripe's signature is over the exact bytes, so this
    must not go through a parsed-model dependency (that would re-serialize and
    every signature would fail). Unmapped events are acknowledged, not errored:
    Stripe retries any non-2xx for days.
    """
    if not settings.stripe_webhook_secret:
        raise AppError(
            "WEBHOOK_NOT_CONFIGURED", "Stripe webhooks aren't configured.", status_code=503
        )

    payload = await request.body()
    try:
        stripe_webhook.verify_signature(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except stripe_webhook.SignatureError as e:
        raise AppError("UNAUTHENTICATED", str(e), status_code=401) from e

    try:
        stripe_event = json.loads(payload)
    except ValueError as e:
        raise AppError("INVALID_EVENT", "Body is not JSON.", status_code=400) from e

    events = stripe_webhook.to_events(stripe_event)
    if not events:
        # Nothing we act on — a 2xx so Stripe stops redelivering it.
        return ok({"ignored": stripe_event.get("type")})

    results = []
    for event in events:
        result = await service.handle_webhook_event(db, provider="stripe", event=event)
        await write_audit(
            db,
            actor_id=None,
            action=f"billing.{event.event_type}",
            entity_type="billing",
            entity_id=result.get("subscription_id") or result.get("payment_id"),
            after=event.model_dump(mode="json"),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        results.append(result)
    return ok({"applied": results})
