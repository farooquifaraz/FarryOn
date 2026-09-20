"""The UAE price list: AED, a renewing subscription, shown only to the UAE.

Faraz's prices (AED 15/25/45 a month, 170/275/500 a year) with the India
caps — at the global caps Plus and Pro would lose money at full use. Unlike
the India list nothing here is one-time: a UAE card renews.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.core.region import region_from_headers
from app.db import seed
from app.db.models import User
from app.modules.billing import service
from app.services import stripe_client
from app.web import pricing

pytestmark = pytest.mark.asyncio

UAE = ("lite_ae", "plus_ae", "pro_ae", "lite_ae_yearly", "plus_ae_yearly", "pro_ae_yearly")


def test_the_catalog_prices_the_uae_tiers_in_fils_with_the_india_caps() -> None:
    s = get_settings()
    for name in UAE:
        assert s.plan_currency(name) == "AED"
        assert s.plan_region(name) == "AE"
        assert not s.plan_is_one_time(name), "a UAE card renews — nothing is bought for a period"
    assert s.plan_price_cents("lite_ae") == 1_500
    assert s.plan_price_cents("plus_ae") == 2_500
    assert s.plan_price_cents("pro_ae") == 4_500
    assert s.plan_price_cents("lite_ae_yearly") == 17_000
    assert s.plan_price_cents("plus_ae_yearly") == 27_500
    assert s.plan_price_cents("pro_ae_yearly") == 50_000
    for ae, india in (("lite_ae", "sathi_in"), ("plus_ae", "plus_in"), ("pro_ae", "pro_in")):
        assert s.plan_limits[ae] == s.plan_limits[india], f"{ae} must carry the India caps"
        assert s.plan_limits[ae + "_yearly"] == s.plan_limits[india]
    assert s.plan_title("lite_ae") == "Lite" and s.plan_title("pro_ae_yearly") == "Pro (yearly)"
    assert set(s.plans_for_region("AE")) == set(UAE)
    assert not set(UAE) & set(s.plans_for_region(None))
    assert not set(UAE) & set(s.plans_for_region("IN"))


def test_every_uae_tier_stays_profitable_at_full_use_and_costs_more_than_india() -> None:
    """The reason for the India caps: the No-Loss Rule (docs/REVENUE_PLAN.md)."""
    s = get_settings()
    voice, scan, search, infra, aed = 0.0093, 0.003, 0.002, 0.25, 3.6725
    for name in ("lite_ae", "plus_ae", "pro_ae"):
        lim = s.plan_limits[name]
        price = s.plan_price_cents(name) / 100
        net = ((price / 1.05) * (1 - 0.029) - 1.0) / aed  # VAT out, Stripe UAE fee
        cost = lim["voice_seconds"] / 60 * voice + lim["image_scans"] * scan + lim["web_searches"] * search + infra
        assert net > cost * 1.4, f"{name}: ${net:.2f} in hand for ${cost:.2f} of cost"
    # per talk-minute, in rupees, the UAE is dearer than India on every tier
    inr_per_aed = 26.12
    for ae, india in (("lite_ae", "sathi_in"), ("plus_ae", "plus_in"), ("pro_ae", "pro_in")):
        ae_min = s.plan_price_cents(ae) / 100 * inr_per_aed / (s.plan_limits[ae]["voice_seconds"] / 60)
        in_min = s.plan_price_cents(india) / 100 / (s.plan_limits[india]["voice_seconds"] / 60)
        assert ae_min > in_min, (ae, ae_min, in_min)


def test_the_uae_rows_are_seeded_in_aed_as_renewing_plans() -> None:
    sold = seed.sold_plans(get_settings())
    assert sold["plus_ae"][0] == 2_500 and sold["plus_ae"][1] == "month" and sold["plus_ae"][3] == "AED"
    assert "billed monthly" in sold["plus_ae"][2] and "(AE)" in sold["plus_ae"][2]
    assert sold["plus_ae_yearly"][1] == "year" and "billed yearly" in sold["plus_ae_yearly"][2]


def test_only_dubai_is_the_uae() -> None:
    assert region_from_headers({"x-timezone": "Asia/Dubai"}) == "AE"
    assert region_from_headers({"x-region": "ae"}) == "AE"
    # the rest of the Gulf is the global list until Faraz says otherwise
    for tz in ("Asia/Riyadh", "Asia/Qatar", "Asia/Muscat", "Asia/Bahrain", "Asia/Kuwait"):
        assert region_from_headers({"x-timezone": tz}) is None, tz
    assert region_from_headers({"x-timezone": "Asia/Kolkata"}) == "IN"


def test_the_website_renders_a_hidden_uae_grid_with_the_saving_on_each_card() -> None:
    html = pricing.uae_cards_html(get_settings())
    assert html.startswith('<div class="plans plans-ae" data-region="AE" hidden')
    assert html.count('class="plan-name"') == 3
    assert '<sup class="code">AED</sup>' in html
    assert 'data-m="15" data-a="170"' in html and 'data-m="45" data-a="500"' in html
    assert 'data-m="per month" data-a="per year"' in html, "a renewing plan, not a period"
    assert "works out at AED 14.17/mo" in html and "works out at AED 41.67/mo" in html
    # 12 × 15 − 170 = 10, 12 × 25 − 275 = 25, 12 × 45 − 500 = 40 — no percentage
    assert "Save AED 10<" in html and "Save AED 25<" in html and "Save AED 40<" in html
    assert "Cancel anytime · VAT included" in html
    assert "₹" not in html and "$" not in html and "one-time" not in html.lower()
    assert html.count("Most popular") == 1
    # and the global list still knows nothing of it
    assert "_ae" not in pricing.plan_cards_html(get_settings())


def test_the_page_carries_all_three_lists() -> None:
    from pathlib import Path

    import app.web.router as web_router

    page = pricing.render(Path(web_router._INDEX).read_text(encoding="utf-8"), get_settings())
    assert "<!--PLAN_CARDS_AE-->" not in page
    assert page.count('class="plans plans-ae"') == 1 and page.count('class="plans plans-in"') == 1
    assert "var REGION_OF={INR:'IN',AED:'AE'}" in page
    assert "See UAE plans (AED)" in page
    assert "'Asia/Dubai':'AED'" in page and "'Asia/Riyadh'" not in page


async def _user(db, email: str) -> User:
    user = User(external_id=f"ext-{email}", email=email, display_name=email)
    db.add(user)
    await db.flush()
    return user


async def test_the_uae_sees_only_its_own_list_and_checks_out_as_a_subscription(db_session, monkeypatch) -> None:
    await seed.seed_plans(db_session, get_settings())
    user = await _user(db_session, "uae@example.com")
    uae = await service.subscription_overview(db_session, user=user, region="AE")
    assert {p["name"] for p in uae["upgrades"]} == set(UAE)
    assert all(p["currency"] == "AED" and p["one_time"] is False for p in uae["upgrades"])
    assert uae["region"] == "AE"
    plus = next(p for p in uae["upgrades"] if p["name"] == "plus_ae")
    assert plus["caps"] == {"talk_minutes": 250, "image_scans": 130, "web_searches": 225}
    world = await service.subscription_overview(db_session, user=user, region=None)
    assert not {p["name"] for p in world["upgrades"]} & set(UAE)

    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "stripe_price_ids", {"plus_ae": "price_ae", "plus": "price_usd"})
    seen: list[dict] = []

    async def fake_session(**kw):
        seen.append(kw)
        return {"url": "https://checkout.stripe/x"}

    monkeypatch.setattr(stripe_client, "create_checkout_session", fake_session)
    await service.create_checkout(db_session, user=user, plan_name="plus_ae", region="AE")
    assert seen[-1]["mode"] == "subscription" and seen[-1]["price_id"] == "price_ae"
