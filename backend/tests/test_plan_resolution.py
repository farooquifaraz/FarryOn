"""A user's quota caps follow the plan they actually pay for.

The gap this closes: quota enforcement read the single global `default_plan`
for everyone. So a paying Pro user was held to the free tier's caps, and the
whole point of charging more for more usage did not exist. `active_plan_name`
resolves the caps-bearing plan from the user's live subscription; these tests
pin that resolution and its effect on `check_quota`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.models import Plan, Subscription, User
from app.modules.billing import service as billing
from app.tools import quota
from app.tools.base import ToolContext

pytestmark = pytest.mark.asyncio


async def _user(db, external_id: str = "u-plan") -> User:
    user = User(external_id=external_id, display_name="Plan Tester")
    db.add(user)
    await db.flush()
    return user


async def _plan(db, name: str, price_cents: int = 999) -> Plan:
    plan = Plan(name=name, price_cents=price_cents, currency="USD", interval="month")
    db.add(plan)
    await db.flush()
    return plan


async def _subscribe(db, user: User, plan: Plan, status: str = "active") -> None:
    db.add(Subscription(user_id=user.id, plan_id=plan.id, status=status))
    await db.flush()


class TestActivePlanName:
    async def test_no_user_gets_the_default(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: SimpleNamespace(default_plan="free"),
        )
        assert await billing.active_plan_name(db_session, None) == "free"

    async def test_a_user_with_no_subscription_gets_the_default(
        self, db_session, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: SimpleNamespace(default_plan="free"),
        )
        user = await _user(db_session)
        assert await billing.active_plan_name(db_session, user.id) == "free"

    async def test_an_active_subscription_wins_over_the_default(
        self, db_session, monkeypatch
    ) -> None:
        # The whole point: default is "free", the user pays for "pro", they get
        # "pro". This was "free" for everyone before.
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: SimpleNamespace(default_plan="free"),
        )
        user = await _user(db_session)
        await _subscribe(db_session, user, await _plan(db_session, "pro"))
        assert await billing.active_plan_name(db_session, user.id) == "pro"

    async def test_trialing_counts_as_active(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: SimpleNamespace(default_plan="free"),
        )
        user = await _user(db_session)
        await _subscribe(
            db_session, user, await _plan(db_session, "plus"), status="trialing"
        )
        assert await billing.active_plan_name(db_session, user.id) == "plus"

    @pytest.mark.parametrize("status", ["past_due", "canceled", "expired"])
    async def test_a_lapsed_subscription_drops_to_the_default(
        self, db_session, monkeypatch, status
    ) -> None:
        # Stop paying and you lose the caps you stopped paying for — you do not
        # keep Pro's limits on a canceled subscription.
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: SimpleNamespace(default_plan="free"),
        )
        user = await _user(db_session)
        await _subscribe(
            db_session, user, await _plan(db_session, "pro"), status=status
        )
        assert await billing.active_plan_name(db_session, user.id) == "free"

    async def test_the_newest_active_subscription_wins(
        self, db_session, monkeypatch
    ) -> None:
        # A user who upgraded plus -> pro has two active rows for a moment; the
        # later one is the one whose caps should apply.
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: SimpleNamespace(default_plan="free"),
        )
        user = await _user(db_session)
        await _subscribe(db_session, user, await _plan(db_session, "plus"))
        await _subscribe(db_session, user, await _plan(db_session, "pro"))
        assert await billing.active_plan_name(db_session, user.id) == "pro"


class TestQuotaUsesTheUsersPlan:
    async def test_pro_user_gets_pro_caps_not_the_default(
        self, db_session, monkeypatch
    ) -> None:
        # default_plan "free" caps image_scans at 1; the Pro plan is unlimited.
        # A Pro user must not be stopped at the free cap.
        settings = SimpleNamespace(
            quota_enforcement_enabled=True,
            default_plan="free",
            plan_limits={"free": {"image_scans": 1}, "pro": {"image_scans": -1}},
        )
        monkeypatch.setattr(quota, "get_settings", lambda: settings)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)

        user = await _user(db_session)
        await _subscribe(db_session, user, await _plan(db_session, "pro"))

        ctx = ToolContext(session=db_session, session_id="s", user_id=user.id)
        for _ in range(5):
            assert await quota.check_quota(ctx, "image_scans") is None

    async def test_free_user_is_still_capped(self, db_session, monkeypatch) -> None:
        settings = SimpleNamespace(
            quota_enforcement_enabled=True,
            default_plan="free",
            plan_limits={"free": {"image_scans": 1}, "pro": {"image_scans": -1}},
        )
        monkeypatch.setattr(quota, "get_settings", lambda: settings)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)

        user = await _user(db_session)  # no subscription

        ctx = ToolContext(session=db_session, session_id="s", user_id=user.id)
        assert await quota.check_quota(ctx, "image_scans") is None  # 1/1
        blocked = await quota.check_quota(ctx, "image_scans")  # over
        assert blocked is not None and blocked["status"] == "quota_exceeded"

    async def test_the_plan_is_resolved_once_and_cached(
        self, db_session, monkeypatch
    ) -> None:
        # Several metered calls in a session must not re-query the plan each
        # time. The resolver is cached on the context.
        settings = SimpleNamespace(
            quota_enforcement_enabled=True,
            default_plan="free",
            plan_limits={"free": {"image_scans": -1}, "pro": {"image_scans": -1}},
        )
        monkeypatch.setattr(quota, "get_settings", lambda: settings)

        calls = {"n": 0}
        real = billing.active_plan_name

        async def counting(db, uid):
            calls["n"] += 1
            return await real(db, uid)

        monkeypatch.setattr(billing, "active_plan_name", counting)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)

        user = await _user(db_session)
        ctx = ToolContext(session=db_session, session_id="s", user_id=user.id)
        for _ in range(4):
            await quota.check_quota(ctx, "image_scans")
        assert calls["n"] == 1, "plan resolved once per context, not per call"
