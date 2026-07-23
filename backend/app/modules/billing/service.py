"""Billing business logic: plan CRUD, subscription queries, revenue math,
and webhook event handling.

Money is always integer cents; MRR normalizes yearly plans to price/12.
Revenue-over-time aggregation happens in Python rather than dialect-specific
SQL date functions (strftime vs to_char) — exact, portable across
SQLite/Postgres, and fine at current volume. Swap to SQL grouping if the
payments table grows past ~100k rows.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import AppError
from app.db.models import Payment, Plan, Subscription, User
from app.modules.billing.schemas import WebhookEvent
from app.logging_conf import get_logger

logger = get_logger(__name__)

PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100

ACTIVE_STATUSES = ("active", "trialing")


def _plan_out(plan: Plan) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "price_cents": plan.price_cents,
        "currency": plan.currency,
        "interval": plan.interval,
        "description": plan.description,
        "features": json.loads(plan.features_json) if plan.features_json else [],
        "is_active": plan.is_active,
    }


# ---- Plan resolution -----------------------------------------------------


async def active_plan_name(db: AsyncSession, user_id: int | None) -> str:
    """The plan whose caps apply to this user right now.

    The name of their active (or trialing) subscription's plan, else the
    configured `default_plan`. This is what quota enforcement must key on — the
    old code read the single global `default_plan` for everyone, which meant a
    paying Pro user got the free tier's caps and an unpaid user got whatever the
    default happened to be. Cost protection that ignores what someone actually
    pays for is not cost protection.

    A user with no id (anonymous session) has no subscription, so they get the
    default. `past_due`/`canceled`/`expired` subscriptions are deliberately not
    active — a lapsed payment drops you to the default tier, it does not keep
    the caps you stopped paying for.
    """
    from app.config import get_settings

    default = get_settings().default_plan
    if user_id is None:
        return default

    row = (
        await db.execute(
            select(Plan.name)
            .join(Subscription, Subscription.plan_id == Plan.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_(ACTIVE_STATUSES),
            )
            .order_by(Subscription.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row or default


# ---- The signed-in user's own view ----------------------------------------


async def subscription_overview(db: AsyncSession, *, user: User) -> dict:
    """What the app's Subscription screen shows: my plan, today's usage, and
    what I could upgrade to.

    One call rather than three because the screen paints from it directly —
    plan identity, each metered resource as ``{used, cap}`` (cap ``-1`` =
    unlimited, ``0`` = not included), and the sellable catalog so the upgrade
    buttons can show real prices. ``checkout_available`` tells the app whether
    tapping Upgrade can work at all (Stripe keys configured) — a button that
    silently can't work is worse than one that says so.
    """
    from datetime import datetime, timezone

    from app.config import get_settings
    from app.tools.quota import user_key_for

    settings = get_settings()
    plan_name = await active_plan_name(db, user.id)
    plan_row = (
        await db.execute(select(Plan).where(Plan.name == plan_name))
    ).scalar_one_or_none()

    from app.db import repo

    usage_row = await repo.get_daily_usage(
        db,
        user_key=user_key_for(user.id, None),
        day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    caps = settings.plan_limits.get(plan_name, {})
    usage = {
        metric: {
            "used": int(getattr(usage_row, metric, 0) or 0) if usage_row else 0,
            "cap": cap,
        }
        for metric, cap in caps.items()
    }

    sellable = [
        p
        for p in await list_plans(db)
        if p["is_active"] and p["name"] != plan_name
    ]
    return {
        "plan": plan_name,
        "price_cents": plan_row.price_cents if plan_row else 0,
        "currency": plan_row.currency if plan_row else "USD",
        "interval": plan_row.interval if plan_row else "month",
        "usage": usage,
        "upgrades": sellable,
        "checkout_available": bool(
            settings.stripe_secret_key and settings.stripe_price_ids
        ),
    }


# ---- Checkout ------------------------------------------------------------


async def create_checkout(db: AsyncSession, *, user: User, plan_name: str) -> dict:
    """Start a Stripe Checkout Session for ``user`` to subscribe to ``plan_name``.

    Returns ``{"url": <hosted checkout url>}``. The caller (the user's own
    request) redirects there; Stripe collects payment and, on success, fires
    the webhook (phase 3) that flips the subscription active. Nothing is written
    to our DB here — a checkout that's started but abandoned must not leave a
    dangling subscription row; the webhook is the single source of that truth.

    Refuses a plan that isn't sold (only `stripe_price_ids` keys are), so the
    free fallback tier and any typo can't reach Stripe. Returns a 503-shaped
    error when Stripe isn't configured, so local testing without keys is clean.
    """
    from app.config import get_settings
    from app.services import stripe_client
    from app.services.stripe_client import StripeError

    settings = get_settings()
    if not settings.stripe_secret_key:
        raise AppError(
            "BILLING_NOT_CONFIGURED",
            "Payments aren't set up yet.",
            status_code=503,
        )
    # One paid subscription per user, full stop. A second checkout would create
    # a second live subscription in Stripe and BOTH would charge every month —
    # the customer pays twice and finds out on a bank statement. Plan changes go
    # through cancel-then-subscribe (or a Stripe portal later), not stacking.
    current = await active_plan_name(db, user.id)
    if current != settings.default_plan:
        raise AppError(
            "ALREADY_SUBSCRIBED",
            f"You're already on the {current} plan. To switch plans, cancel "
            "the current one first.",
            status_code=409,
        )
    price_id = settings.stripe_price_ids.get(plan_name)
    if not price_id:
        raise AppError(
            "UNKNOWN_PLAN",
            f"'{plan_name}' isn't a plan you can subscribe to.",
            status_code=400,
        )

    try:
        session = await stripe_client.create_checkout_session(
            secret_key=settings.stripe_secret_key,
            price_id=price_id,
            success_url=settings.stripe_success_url,
            cancel_url=settings.stripe_cancel_url,
            client_reference_id=str(user.id),
            customer_email=user.email,
            metadata={"user_id": str(user.id), "plan": plan_name},
        )
    except StripeError as e:
        raise AppError(
            "STRIPE_ERROR",
            e.args[0] if e.args else "Checkout could not be started.",
            status_code=502,
        ) from e

    url = session.get("url")
    if not url:
        raise AppError("STRIPE_ERROR", "Stripe did not return a checkout URL.", status_code=502)
    logger.info("billing.checkout_started", user_id=user.id, plan=plan_name)
    return {"url": url}


# ---- Plans ---------------------------------------------------------------


async def list_plans(db: AsyncSession) -> list[dict]:
    plans = list(
        (await db.execute(select(Plan).order_by(Plan.price_cents))).scalars()
    )
    return [_plan_out(p) for p in plans]


async def create_plan(
    db: AsyncSession,
    *,
    name: str,
    price_cents: int,
    currency: str,
    interval: str,
    description: str | None,
    features: list[str],
) -> dict:
    existing = (
        await db.execute(select(Plan).where(Plan.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError("PLAN_EXISTS", "A plan with this name already exists.", status_code=409)
    plan = Plan(
        name=name,
        price_cents=price_cents,
        currency=currency,
        interval=interval,
        description=description,
        features_json=json.dumps(features),
    )
    db.add(plan)
    await db.flush()
    return _plan_out(plan)


async def update_plan(
    db: AsyncSession,
    plan_id: int,
    *,
    price_cents: int | None,
    description: str | None,
    features: list[str] | None,
    is_active: bool | None,
) -> dict:
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise AppError("NOT_FOUND", "Plan not found.", status_code=404)
    if price_cents is not None:
        plan.price_cents = price_cents
    if description is not None:
        plan.description = description
    if features is not None:
        plan.features_json = json.dumps(features)
    if is_active is not None:
        plan.is_active = is_active
    await db.flush()
    return _plan_out(plan)


# ---- Subscriptions ---------------------------------------------------------


async def list_subscriptions(
    db: AsyncSession,
    *,
    status_filter: str | None,
    plan_filter: str | None,
    search: str | None,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)

    query = (
        select(Subscription, User, Plan)
        .join(User, User.id == Subscription.user_id)
        .join(Plan, Plan.id == Subscription.plan_id)
    )
    count_query = (
        select(func.count())
        .select_from(Subscription)
        .join(User, User.id == Subscription.user_id)
        .join(Plan, Plan.id == Subscription.plan_id)
    )

    if status_filter:
        query = query.where(Subscription.status == status_filter)
        count_query = count_query.where(Subscription.status == status_filter)
    if plan_filter:
        query = query.where(Plan.name == plan_filter)
        count_query = count_query.where(Plan.name == plan_filter)
    if search:
        like = f"%{search.lower()}%"
        cond = func.lower(User.email).like(like)
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = (await db.execute(count_query)).scalar_one()
    rows = (
        await db.execute(
            query.order_by(Subscription.started_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    # Lifetime-paid per user for the visible page only (one grouped query).
    user_ids = [user.id for _, user, _ in rows]
    paid_by_user: dict[int, int] = {}
    if user_ids:
        paid_rows = await db.execute(
            select(Payment.user_id, func.sum(Payment.amount_cents))
            .where(Payment.user_id.in_(user_ids), Payment.status == "succeeded")
            .group_by(Payment.user_id)
        )
        paid_by_user = {uid: total_cents for uid, total_cents in paid_rows.all()}

    items = [
        {
            "id": sub.id,
            "user_id": user.id,
            "user_email": user.email,
            "user_display_name": user.display_name,
            "plan_name": plan.name,
            "status": sub.status,
            "started_at": sub.started_at.isoformat(),
            "current_period_end": (
                sub.current_period_end.isoformat() if sub.current_period_end else None
            ),
            "lifetime_paid_cents": paid_by_user.get(user.id, 0),
        }
        for sub, user, plan in rows
    ]
    return items, total


# ---- Revenue ---------------------------------------------------------------


async def revenue_summary(db: AsyncSession) -> dict:
    total_revenue_cents = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
                Payment.status == "succeeded"
            )
        )
    ).scalar_one()

    active_subs = (
        await db.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.status.in_(ACTIVE_STATUSES))
        )
    ).all()

    mrr_cents = 0
    active_by_plan: dict[str, dict] = defaultdict(lambda: {"count": 0, "mrr_cents": 0})
    for sub, plan in active_subs:
        monthly = plan.price_cents if plan.interval == "month" else plan.price_cents // 12
        mrr_cents += monthly
        active_by_plan[plan.name]["count"] += 1
        active_by_plan[plan.name]["mrr_cents"] += monthly

    # Revenue over time: succeeded payments grouped by YYYY-MM in Python
    # (portable across dialects — see module docstring).
    payments = (
        await db.execute(
            select(Payment.paid_at, Payment.amount_cents).where(Payment.status == "succeeded")
        )
    ).all()
    by_month: dict[str, int] = defaultdict(int)
    for paid_at, amount in payments:
        by_month[paid_at.strftime("%Y-%m")] += amount

    return {
        "total_revenue_cents": total_revenue_cents,
        "mrr_cents": mrr_cents,
        "active_subscribers": len(active_subs),
        "revenue_by_plan": [
            {"plan": name, **data} for name, data in sorted(active_by_plan.items())
        ],
        "revenue_over_time": [
            {"month": month, "amount_cents": amount}
            for month, amount in sorted(by_month.items())
        ],
    }


async def list_transactions(
    db: AsyncSession, *, status_filter: str | None, page: int, page_size: int
) -> tuple[list[dict], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)

    query = select(Payment, User).join(User, User.id == Payment.user_id)
    count_query = select(func.count()).select_from(Payment)
    if status_filter:
        query = query.where(Payment.status == status_filter)
        count_query = count_query.where(Payment.status == status_filter)

    total = (await db.execute(count_query)).scalar_one()
    rows = (
        await db.execute(
            query.order_by(Payment.paid_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        {
            "id": payment.id,
            "user_id": user.id,
            "user_email": user.email,
            "amount_cents": payment.amount_cents,
            "currency": payment.currency,
            "status": payment.status,
            "paid_at": payment.paid_at.isoformat(),
            "provider": payment.provider,
            "provider_payment_id": payment.provider_payment_id,
        }
        for payment, user in rows
    ]
    return items, total


# ---- Webhook handling --------------------------------------------------------


async def handle_webhook_event(
    db: AsyncSession, *, provider: str, event: WebhookEvent
) -> dict:
    """Apply one normalized provider event to subscriptions/payments.

    Idempotent on payments: a duplicate ``provider_payment_id`` is skipped
    (providers redeliver webhooks; unique index enforces it at the DB layer
    too). Unknown user/plan errors out so the provider retries after the
    operator fixes the mapping.
    """
    user = await db.get(User, event.user_id)
    if user is None:
        raise AppError("NOT_FOUND", f"Unknown user_id {event.user_id}", status_code=404)

    if event.event_type.startswith("subscription."):
        return await _handle_subscription_event(db, provider=provider, event=event)
    return await _handle_payment_event(db, provider=provider, event=event)


async def _get_subscription(
    db: AsyncSession, *, provider: str, event: WebhookEvent
) -> Subscription | None:
    if event.provider_subscription_id:
        row = (
            await db.execute(
                select(Subscription).where(
                    Subscription.provider == provider,
                    Subscription.provider_subscription_id == event.provider_subscription_id,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
    # Fall back to the user's most recent subscription.
    return (
        (
            await db.execute(
                select(Subscription)
                .where(Subscription.user_id == event.user_id)
                .order_by(Subscription.started_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def _handle_subscription_event(
    db: AsyncSession, *, provider: str, event: WebhookEvent
) -> dict:
    now = datetime.now(timezone.utc)

    if event.event_type == "subscription.created":
        if not event.plan_name:
            raise AppError("INVALID_EVENT", "plan_name required for subscription.created", status_code=400)
        # Idempotent on the provider's subscription id: Stripe redelivers
        # checkout.session.completed, and without this each redelivery would add
        # another active subscription row for the same paid subscription.
        if event.provider_subscription_id:
            dup = (
                await db.execute(
                    select(Subscription).where(
                        Subscription.provider == provider,
                        Subscription.provider_subscription_id
                        == event.provider_subscription_id,
                    )
                )
            ).scalar_one_or_none()
            if dup is not None:
                return {"subscription_id": dup.id, "status": dup.status, "duplicate": True}
        plan = (
            await db.execute(select(Plan).where(Plan.name == event.plan_name))
        ).scalar_one_or_none()
        if plan is None:
            raise AppError("NOT_FOUND", f"Unknown plan: {event.plan_name}", status_code=404)
        sub = Subscription(
            user_id=event.user_id,
            plan_id=plan.id,
            status="active",
            current_period_end=event.period_end,
            provider=provider,
            provider_subscription_id=event.provider_subscription_id,
        )
        db.add(sub)
        await db.flush()
        logger.info("billing.subscription_created", user_id=event.user_id, plan=plan.name)
        return {"subscription_id": sub.id, "status": sub.status}

    sub = await _get_subscription(db, provider=provider, event=event)
    if sub is None:
        raise AppError("NOT_FOUND", "No subscription found for this event.", status_code=404)

    if event.event_type == "subscription.renewed":
        sub.status = "active"
        sub.current_period_end = event.period_end
    elif event.event_type == "subscription.canceled":
        sub.status = "canceled"
        sub.canceled_at = now
    elif event.event_type == "subscription.past_due":
        sub.status = "past_due"

    await db.flush()
    logger.info("billing.subscription_updated", subscription_id=sub.id, status=sub.status)
    return {"subscription_id": sub.id, "status": sub.status}


async def _handle_payment_event(
    db: AsyncSession, *, provider: str, event: WebhookEvent
) -> dict:
    if event.event_type == "payment.refunded":
        if not event.provider_payment_id:
            raise AppError("INVALID_EVENT", "provider_payment_id required for refund", status_code=400)
        payment = (
            await db.execute(
                select(Payment).where(Payment.provider_payment_id == event.provider_payment_id)
            )
        ).scalar_one_or_none()
        if payment is None:
            raise AppError("NOT_FOUND", "Unknown payment for refund.", status_code=404)
        payment.status = "refunded"
        await db.flush()
        logger.info("billing.payment_refunded", payment_id=payment.id)
        return {"payment_id": payment.id, "status": "refunded"}

    if event.amount_cents is None:
        raise AppError("INVALID_EVENT", "amount_cents required for payment events", status_code=400)

    # Idempotency: skip redelivered events for a payment we already recorded.
    if event.provider_payment_id:
        existing = (
            await db.execute(
                select(Payment).where(Payment.provider_payment_id == event.provider_payment_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"payment_id": existing.id, "status": existing.status, "duplicate": True}

    sub = await _get_subscription(db, provider=provider, event=event)
    payment = Payment(
        subscription_id=sub.id if sub else None,
        user_id=event.user_id,
        amount_cents=event.amount_cents,
        currency=event.currency,
        status="succeeded" if event.event_type == "payment.succeeded" else "failed",
        provider=provider,
        provider_payment_id=event.provider_payment_id,
    )
    db.add(payment)
    await db.flush()
    logger.info(
        "billing.payment_recorded",
        payment_id=payment.id,
        status=payment.status,
        amount_cents=event.amount_cents,
    )
    return {"payment_id": payment.id, "status": payment.status}
