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
    assert set(seed.PLAN_CATALOG) <= set(plans)
    assert plans["plus"].price_cents == 999
    assert plans["pro"].price_cents == 1999
    assert plans["plus"].currency == "USD"
    assert plans["pro"].is_active is True


async def test_running_twice_does_not_duplicate(db_session) -> None:
    await seed.seed_plans(db_session)
    await seed.seed_plans(db_session)

    rows = (
        await db_session.execute(select(Plan).where(Plan.name == "pro"))
    ).scalars().all()
    assert len(rows) == 1


async def test_a_price_change_reaches_an_existing_row(db_session, monkeypatch) -> None:
    # The trap: seeding that only ever creates would leave a stale price on the
    # row forever. Change the catalog, re-seed, the DB must follow.
    await seed.seed_plans(db_session)
    monkeypatch.setitem(seed.PLAN_CATALOG, "pro", (2499, "month", "Pro — repriced."))

    await seed.seed_plans(db_session)

    plans = await _plans_by_name(db_session)
    assert plans["pro"].price_cents == 2499
    assert plans["pro"].description == "Pro — repriced."


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
    for name in seed.PLAN_CATALOG:
        assert name in limits, f"plan {name!r} is sold but has no quota caps"
        assert "voice_seconds" in limits[name]
