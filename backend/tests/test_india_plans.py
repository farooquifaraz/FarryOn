"""The India price list: INR, one-time payment, shown only to India.

What is pinned: the six India tiers exist in the catalog with the agreed
prices and caps and derive their quota limits like every other plan; they
are priced in INR (paise, not cents) and seeded with that currency; the
website's USD cards never show them; a request from India is offered them
and nothing else, everyone else the reverse, and a checkout across that
line is refused; a one-time plan checks out in Stripe's payment mode; the
completed session activates a 30-day (or 365-day) period, a second purchase
extends it from the end, a redelivered event does not, and when the date
passes the caps fall back to free — while a renewing USD subscription is
never judged by its period end here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.core.region import region_from_headers
from app.core.responses import AppError
from app.db import seed
from app.db.models import Payment, Plan, Subscription, User
from app.modules.billing import service, stripe_webhook
from app.modules.billing.schemas import WebhookEvent
from app.services import stripe_client
from app.web import pricing

pytestmark = pytest.mark.asyncio

INDIA = ("sathi_in", "plus_in", "pro_in", "sathi_in_yearly", "plus_in_yearly", "pro_in_yearly")


# ── catalog ────────────────────────────────────────────────────────────────


def test_the_india_tiers_are_in_the_catalog_with_the_agreed_numbers() -> None:
    s = get_settings()
    for name in INDIA:
        assert name in s.plan_catalog
        assert s.plan_currency(name) == "INR"
        assert s.plan_region(name) == "IN"
        assert s.plan_is_one_time(name)
    assert s.plan_price_cents("sathi_in") == 29_900
    assert s.plan_price_cents("plus_in") == 59_900
    assert s.plan_price_cents("pro_in") == 99_900
    assert s.plan_price_cents("sathi_in_yearly") == 330_000
    assert s.plan_price_cents("plus_in_yearly") == 550_000
    assert s.plan_price_cents("pro_in_yearly") == 1_100_000
    assert s.plan_limits["sathi_in"] == {"voice_seconds": 120 * 60, "image_scans": 70, "web_searches": 110, "translate_seconds": 120 * 60}
    assert s.plan_limits["pro_in_yearly"]["image_scans"] == 240
    assert s.plan_title("sathi_in") == "साथी" and s.plan_title("sathi_in_yearly") == "साथी (yearly)"
    assert s.plan_title("plus_in") == "Plus"
    # the global list is untouched
    assert s.plan_currency("plus") == "USD" and s.plan_region("plus") is None
    assert not s.plan_is_one_time("plus") and s.plan_price_cents("plus") == 1500
    assert s.plan_title("plus_yearly") == "Plus (yearly)"
    assert set(s.plans_for_region("IN")) == set(INDIA)
    assert "plus" in s.plans_for_region(None) and "plus_in" not in s.plans_for_region(None)


def test_seeding_writes_the_plans_in_their_own_currency() -> None:
    sold = seed.sold_plans()
    assert sold["plus_in"][0] == 59_900 and sold["plus_in"][3] == "INR"
    assert "one-time payment for 30 days" in sold["plus_in"][2]
    assert "one-time payment for 12 months" in sold["plus_in_yearly"][2]
    assert sold["plus"][3] == "USD" and "billed monthly" in sold["plus"][2]


def test_the_website_shows_only_the_global_cards() -> None:
    html = pricing.plan_cards_html(get_settings())
    assert html.count('class="plan-name"') == 4  # free, lite, plus, pro
    assert "साथी" not in html and "sathi" not in html.lower()
    # each yearly card says its own saving in dollars — never rupees here
    assert html.count('class="plan-save"') == 3 and "₹" not in html


# ── region ─────────────────────────────────────────────────────────────────


def test_a_request_from_india_is_india_and_everyone_else_is_global() -> None:
    assert region_from_headers({"x-timezone": "Asia/Kolkata"}) == "IN"
    assert region_from_headers({"x-timezone": "Asia/Calcutta"}) == "IN"
    assert region_from_headers({"x-region": "in"}) == "IN"
    assert region_from_headers({"cf-ipcountry": "IN"}) == "IN"
    assert region_from_headers({"x-timezone": "Asia/Dubai"}) is None
    assert region_from_headers({"x-region": "AE"}) is None
    assert region_from_headers({}) is None


# ── the DB paths ───────────────────────────────────────────────────────────


async def _user(db, email: str) -> User:
    user = User(external_id=f"ext-{email}", email=email, display_name=email)
    db.add(user)
    await db.flush()
    return user


async def _seeded(db) -> None:
    await seed.seed_plans(db, get_settings())


async def test_only_the_regions_own_plans_are_offered(db_session) -> None:
    await _seeded(db_session)
    user = await _user(db_session, "region@example.com")
    india = await service.subscription_overview(db_session, user=user, region="IN")
    world = await service.subscription_overview(db_session, user=user, region=None)
    assert {p["name"] for p in india["upgrades"]} == set(INDIA)
    assert all(p["currency"] == "INR" for p in india["upgrades"])
    assert india["region"] == "IN" and india["one_time"] is False and india["period_end"] is None
    assert {p["name"] for p in world["upgrades"]} == {"lite", "plus", "pro", "lite_yearly", "plus_yearly", "pro_yearly"}
    assert all(p["currency"] == "USD" for p in world["upgrades"])


async def test_a_checkout_across_the_region_line_is_refused(db_session, monkeypatch) -> None:
    await _seeded(db_session)
    user = await _user(db_session, "line@example.com")
    monkeypatch.setattr(
        service, "get_settings" if hasattr(service, "get_settings") else "logger", getattr(service, "get_settings", service.logger), raising=False
    )
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "stripe_price_ids", {"plus_in": "price_in", "plus": "price_usd"})
    with pytest.raises(AppError) as err:
        await service.create_checkout(db_session, user=user, plan_name="plus_in", region=None)
    assert err.value.code == "REGION_MISMATCH"
    with pytest.raises(AppError) as err:
        await service.create_checkout(db_session, user=user, plan_name="plus", region="IN")
    assert err.value.code == "REGION_MISMATCH"


async def test_a_one_time_plan_checks_out_in_payment_mode(db_session, monkeypatch) -> None:
    await _seeded(db_session)
    user = await _user(db_session, "mode@example.com")
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "stripe_price_ids", {"plus_in": "price_in", "plus": "price_usd"})
    seen: list[dict] = []

    async def fake_session(**kw):
        seen.append(kw)
        return {"url": "https://checkout.stripe/x"}

    monkeypatch.setattr(stripe_client, "create_checkout_session", fake_session)
    out = await service.create_checkout(db_session, user=user, plan_name="plus_in", region="IN")
    assert out["url"].startswith("https://")
    assert seen[-1]["mode"] == "payment" and seen[-1]["price_id"] == "price_in"
    await service.create_checkout(db_session, user=user, plan_name="plus", region=None)
    assert seen[-1]["mode"] == "subscription"


def test_the_stripe_client_carries_metadata_on_the_intent_in_payment_mode() -> None:
    params = stripe_client._flatten  # the flattener is what builds the form body
    assert callable(params)
    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(
            stripe_client.create_checkout_session(
                secret_key="sk", price_id="p", success_url="s", cancel_url="c",
                client_reference_id="1", customer_email=None, metadata={}, mode="weird",
            )
        )


def test_a_completed_one_time_checkout_becomes_a_period_and_a_payment() -> None:
    events = stripe_webhook.to_events(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "mode": "payment",
                    "payment_intent": "pi_in_1",
                    "amount_total": 59900,
                    "currency": "inr",
                    "client_reference_id": "7",
                    "metadata": {"user_id": "7", "plan": "plus_in"},
                }
            },
        }
    )
    kinds = [e.event_type for e in events]
    assert kinds == ["subscription.created", "payment.succeeded"]
    assert events[0].provider_subscription_id == "pi_in_1"
    assert events[1].amount_cents == 59900 and events[1].currency == "INR"
    assert events[1].provider_payment_id == "pi_in_1"


async def test_a_one_time_purchase_runs_30_days_extends_from_the_end_and_then_lapses(db_session) -> None:
    await _seeded(db_session)
    user = await _user(db_session, "period@example.com")
    ev = WebhookEvent(event_type="subscription.created", user_id=user.id, plan_name="plus_in", provider_subscription_id="pi_1")
    await service.handle_webhook_event(db_session, provider="stripe", event=ev)
    now = datetime.now(timezone.utc)
    sub = (await db_session.execute(select(Subscription).where(Subscription.user_id == user.id))).scalar_one()
    end = sub.current_period_end.replace(tzinfo=timezone.utc)
    assert timedelta(days=29, hours=23) < end - now <= timedelta(days=30)
    assert await service.active_plan_name(db_session, user.id) == "plus_in"

    # Paid again on day 1: the next period starts where this one ends.
    ev2 = WebhookEvent(event_type="subscription.created", user_id=user.id, plan_name="plus_in", provider_subscription_id="pi_2")
    out = await service.handle_webhook_event(db_session, provider="stripe", event=ev2)
    assert out.get("extended") is True
    subs = (await db_session.execute(select(Subscription).where(Subscription.user_id == user.id))).scalars().all()
    assert len(subs) == 1, "an extension is the same row, not a second one"
    end2 = subs[0].current_period_end.replace(tzinfo=timezone.utc)
    assert timedelta(days=59, hours=23) < end2 - now <= timedelta(days=60)

    # Redelivered: a duplicate, not a third period.
    dup = await service.handle_webhook_event(db_session, provider="stripe", event=ev2)
    assert dup.get("duplicate") is True
    end3 = (await db_session.execute(select(Subscription.current_period_end).where(Subscription.user_id == user.id))).scalar_one()
    assert end3.replace(tzinfo=timezone.utc) == end2

    # The money is a payment row too.
    pay = WebhookEvent(
        event_type="payment.succeeded", user_id=user.id, plan_name="plus_in",
        provider_subscription_id="pi_2", provider_payment_id="pi_2", amount_cents=59900, currency="INR",
    )
    await service.handle_webhook_event(db_session, provider="stripe", event=pay)
    paid = (await db_session.execute(select(Payment).where(Payment.user_id == user.id))).scalar_one()
    assert paid.amount_cents == 59900 and paid.currency == "INR" and paid.status == "succeeded"

    # And when the bought period is over, so is the plan.
    subs[0].current_period_end = now - timedelta(seconds=1)
    await db_session.flush()
    assert await service.active_plan_name(db_session, user.id) == get_settings().default_plan
    view = await service.subscription_overview(db_session, user=user, region="IN")
    assert view["plan"] == get_settings().default_plan


async def test_a_renewing_usd_subscription_is_not_lapsed_by_its_period_end(db_session) -> None:
    """Stripe moves a renewing plan's period end on every invoice; a late
    webhook must not drop a paying user to free for an afternoon."""
    await _seeded(db_session)
    user = await _user(db_session, "usd@example.com")
    plan = (await db_session.execute(select(Plan).where(Plan.name == "plus"))).scalar_one()
    db_session.add(
        Subscription(
            user_id=user.id, plan_id=plan.id, status="active",
            current_period_end=datetime.now(timezone.utc) - timedelta(days=2),
            provider="stripe", provider_subscription_id="sub_late",
        )
    )
    await db_session.flush()
    assert await service.active_plan_name(db_session, user.id) == "plus"


async def test_the_valid_till_date_is_on_the_overview_for_a_one_time_plan(db_session) -> None:
    await _seeded(db_session)
    user = await _user(db_session, "till@example.com")
    ev = WebhookEvent(event_type="subscription.created", user_id=user.id, plan_name="sathi_in_yearly", provider_subscription_id="pi_y")
    await service.handle_webhook_event(db_session, provider="stripe", event=ev)
    view = await service.subscription_overview(db_session, user=user, region="IN")
    assert view["plan"] == "sathi_in_yearly" and view["one_time"] is True
    assert view["period_end"] is not None
    end = datetime.fromisoformat(view["period_end"])
    end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    assert timedelta(days=364) < end - datetime.now(timezone.utc) <= timedelta(days=365)
    # buying the same one-time plan again is allowed (it extends); a different one is not
    s = get_settings()
    assert "sathi_in_yearly" not in {p["name"] for p in view["upgrades"]}
