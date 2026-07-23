"""GET /billing/me — what the app's Subscription screen paints from.

The claims that matter: the plan is the USER's plan (not the global default),
usage is today's real meter, caps ride along per metric, and the upgrade list
never offers the plan you're already on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db import repo
from app.db.models import Plan, Subscription, User
from app.modules.billing import service

pytestmark = pytest.mark.asyncio


def _settings(monkeypatch, **over):
    fields = {
        "default_plan": "free",
        "plan_limits": {
            "free": {"voice_seconds": 180, "image_scans": 2},
            "plus": {"voice_seconds": 420, "image_scans": 20},
            "pro": {"voice_seconds": 900, "image_scans": -1},
        },
        "stripe_secret_key": None,
        "stripe_price_ids": {},
    }
    fields.update(over)
    settings = SimpleNamespace(**fields)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return settings


async def _user(db) -> User:
    user = User(external_id="overview-user", email="o@example.com")
    db.add(user)
    await db.flush()
    return user


async def _catalog(db) -> dict[str, Plan]:
    plans = {
        "plus": Plan(name="plus", price_cents=999, currency="USD", interval="month"),
        "pro": Plan(name="pro", price_cents=1999, currency="USD", interval="month"),
    }
    for p in plans.values():
        db.add(p)
    await db.flush()
    return plans


async def test_a_free_user_sees_free_with_zero_usage(db_session, monkeypatch) -> None:
    _settings(monkeypatch)
    await _catalog(db_session)
    user = await _user(db_session)

    out = await service.subscription_overview(db_session, user=user)

    assert out["plan"] == "free"
    assert out["price_cents"] == 0
    assert out["usage"]["voice_seconds"] == {"used": 0, "cap": 180}
    assert out["usage"]["image_scans"] == {"used": 0, "cap": 2}


async def test_todays_usage_is_reflected(db_session, monkeypatch) -> None:
    _settings(monkeypatch)
    await _catalog(db_session)
    user = await _user(db_session)
    await repo.bump_daily_usage(
        db_session,
        user_key=f"u{user.id}",
        day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        voice_seconds=95,
        image_scans=1,
    )

    out = await service.subscription_overview(db_session, user=user)

    assert out["usage"]["voice_seconds"]["used"] == 95
    assert out["usage"]["image_scans"]["used"] == 1


async def test_a_subscriber_sees_their_plan_price_and_caps(
    db_session, monkeypatch
) -> None:
    _settings(monkeypatch)
    plans = await _catalog(db_session)
    user = await _user(db_session)
    db_session.add(
        Subscription(user_id=user.id, plan_id=plans["pro"].id, status="active")
    )
    await db_session.flush()

    out = await service.subscription_overview(db_session, user=user)

    assert out["plan"] == "pro"
    assert out["price_cents"] == 1999
    assert out["usage"]["image_scans"]["cap"] == -1, "unlimited must survive as -1"


async def test_upgrades_never_include_the_current_plan(db_session, monkeypatch) -> None:
    _settings(monkeypatch)
    plans = await _catalog(db_session)
    user = await _user(db_session)
    db_session.add(
        Subscription(user_id=user.id, plan_id=plans["plus"].id, status="active")
    )
    await db_session.flush()

    out = await service.subscription_overview(db_session, user=user)

    names = [p["name"] for p in out["upgrades"]]
    assert "plus" not in names, "you can't upgrade to what you already have"
    assert "pro" in names


async def test_checkout_available_tracks_stripe_config(db_session, monkeypatch) -> None:
    # The app decides whether Upgrade can work at all from this flag.
    await _catalog(db_session)
    user = await _user(db_session)

    _settings(monkeypatch)  # no keys
    off = await service.subscription_overview(db_session, user=user)
    assert off["checkout_available"] is False

    _settings(
        monkeypatch,
        stripe_secret_key="sk_test_x",
        stripe_price_ids={"plus": "price_1", "pro": "price_2"},
    )
    on = await service.subscription_overview(db_session, user=user)
    assert on["checkout_available"] is True
