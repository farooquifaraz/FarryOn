"""Seeding the billing catalog is idempotent and reprices in place.

These run on every deploy via run_seed, so the two things that matter are: a
second run does not duplicate a plan, and a price changed in the catalog
reaches an already-seeded row (rather than being ignored because the row
exists). Custom plans an operator added must survive both.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import seed
from app.db.models import Plan

pytestmark = pytest.mark.asyncio


async def _plans_by_name(db) -> dict[str, Plan]:
    rows = (await db.execute(select(Plan))).scalars().all()
    return {p.name: p for p in rows}


async def test_seeds_the_catalog(db_session) -> None:
    await seed.seed_plans(db_session)

    plans = await _plans_by_name(db_session)
    assert set(seed.sold_plans()) <= set(plans)
    # Prices come from Settings.plan_catalog (lite $6, plus $15, pro $25 —
    # repriced 2026-09-05 against the measured cost per talk-minute).
    assert plans["lite"].price_cents == 600
    assert plans["plus"].price_cents == 1500
    assert plans["pro"].price_cents == 2500
    assert plans["plus"].currency == "USD"
    assert plans["pro"].is_active is True
    # The free/trial tier is a fallback, not a billable row.
    assert "free" not in plans


async def test_running_twice_does_not_duplicate(db_session) -> None:
    await seed.seed_plans(db_session)
    await seed.seed_plans(db_session)

    rows = (
        await db_session.execute(select(Plan).where(Plan.name == "pro"))
    ).scalars().all()
    assert len(rows) == 1


async def test_a_price_change_reaches_an_existing_row(db_session) -> None:
    # The trap: seeding that only ever creates would leave a stale price on the
    # row forever. Change the catalog, re-seed, the DB must follow.
    await seed.seed_plans(db_session)

    from app.config import get_settings

    base = get_settings()
    catalog = {name: dict(row) for name, row in base.plan_catalog.items()}
    catalog["pro"]["price_usd"] = 24.99
    repriced = base.model_copy(update={"plan_catalog": catalog})

    await seed.seed_plans(db_session, repriced)

    plans = await _plans_by_name(db_session)
    assert plans["pro"].price_cents == 2499


async def test_a_custom_plan_is_left_alone(db_session) -> None:
    # An operator can add a plan through the admin panel; re-seeding must not
    # delete or disturb it.
    db_session.add(
        Plan(name="lifetime", price_cents=9900, currency="USD", interval="once")
    )
    await db_session.flush()

    await seed.seed_plans(db_session)

    plans = await _plans_by_name(db_session)
    assert "lifetime" in plans
    assert plans["lifetime"].price_cents == 9900


async def test_seeded_plan_names_match_the_quota_caps(db_session) -> None:
    # Money lives in the plans table, caps in Settings.plan_limits, joined by
    # name. A plan sold with no caps defined would be unlimited by accident, so
    # every catalog name must have a caps entry.
    from app.config import get_settings

    limits = get_settings().plan_limits
    for name in seed.sold_plans():
        assert name in limits, f"plan {name!r} is sold but has no quota caps"
        assert "voice_seconds" in limits[name]


async def test_yearly_plans_are_sold_alongside_monthly(db_session) -> None:
    """A year is a billing interval, not a different product.

    The yearly rows carry the SAME monthly allowances — a year-sized bucket
    would let someone spend twelve months of the most expensive thing we sell
    in a weekend — and differ only in price and interval.
    """
    from app.config import get_settings

    await seed.seed_plans(db_session)
    plans = await _plans_by_name(db_session)
    settings = get_settings()

    for monthly, yearly, cents in (
        ("lite", "lite_yearly", 6500),
        ("plus", "plus_yearly", 16500),
        ("pro", "pro_yearly", 27500),
    ):
        assert plans[yearly].price_cents == cents
        assert plans[yearly].interval == "year"
        assert plans[monthly].interval == "month"
        # Same caps, so a month of a yearly plan buys exactly what a month of
        # the monthly plan buys.
        assert settings.plan_limits[yearly] == settings.plan_limits[monthly]
        # And it is cheaper than paying month by month, or nobody would take it.
        assert cents < plans[monthly].price_cents * 12


async def test_a_yearly_plan_reads_as_a_name_not_a_key(db_session) -> None:
    from app.config import get_settings

    settings = get_settings()
    assert settings.plan_title("plus_yearly") == "Plus (yearly)"
    assert settings.plan_title("plus") == "Plus"
    assert settings.plan_interval("pro_yearly") == "year"
    assert settings.plan_interval("pro") == "month"
    # A yearly plan is NOT a trial: its caps still reset every month.
    assert settings.usage_window("pro_yearly") == "month"
