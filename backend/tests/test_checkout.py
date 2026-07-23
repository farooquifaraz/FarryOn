"""Starting a Stripe Checkout Session.

Phase 2 of billing. No real Stripe call is made — the http client is mocked —
so these assert the two things that matter without keys: that we hand Stripe
exactly the right parameters (a wrong price id or a missing client_reference_id
means the webhook can never map the payment back to a user), and that the
failure paths are clean (not configured, unknown plan, Stripe down).
"""

from __future__ import annotations

import pytest

from app.core.responses import AppError
from app.db.models import User
from app.modules.billing import service
from app.services import stripe_client

pytestmark = pytest.mark.asyncio


def _configure(monkeypatch, **over):
    """A settings stub with Stripe wired, unless a test overrides a field."""
    from types import SimpleNamespace

    fields = {
        "default_plan": "free",
        "stripe_secret_key": "sk_test_x",
        "stripe_price_ids": {"plus": "price_plus", "pro": "price_pro"},
        "stripe_success_url": "https://app/success?session_id={CHECKOUT_SESSION_ID}",
        "stripe_cancel_url": "https://app/cancel",
    }
    fields.update(over)
    settings = SimpleNamespace(**fields)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return settings


async def _user(db, email: str | None = "sub@example.com") -> User:
    user = User(external_id="sub-user", email=email, display_name="Sub")
    db.add(user)
    await db.flush()
    return user


def _fake_stripe(monkeypatch, capture: dict, *, url: str | None = "https://checkout.stripe/x"):
    async def fake(**kwargs):
        capture.update(kwargs)
        return {"id": "cs_test_1", "url": url} if url is not None else {"id": "cs_test_1"}

    monkeypatch.setattr(stripe_client, "create_checkout_session", fake)


class TestHappyPath:
    async def test_returns_the_hosted_url(self, db_session, monkeypatch) -> None:
        _configure(monkeypatch)
        _fake_stripe(monkeypatch, {})
        user = await _user(db_session)

        out = await service.create_checkout(db_session, user=user, plan_name="pro")

        assert out == {"url": "https://checkout.stripe/x"}

    async def test_hands_stripe_the_right_parameters(self, db_session, monkeypatch) -> None:
        # A wrong price id charges the wrong amount; a missing client_reference_id
        # / metadata means phase 3 can't tie the subscription to this user.
        _configure(monkeypatch)
        cap: dict = {}
        _fake_stripe(monkeypatch, cap)
        user = await _user(db_session)

        await service.create_checkout(db_session, user=user, plan_name="pro")

        assert cap["price_id"] == "price_pro"
        assert cap["client_reference_id"] == str(user.id)
        assert cap["customer_email"] == "sub@example.com"
        assert cap["metadata"] == {"user_id": str(user.id), "plan": "pro"}
        assert cap["secret_key"] == "sk_test_x"

    async def test_plus_maps_to_the_plus_price(self, db_session, monkeypatch) -> None:
        _configure(monkeypatch)
        cap: dict = {}
        _fake_stripe(monkeypatch, cap)
        user = await _user(db_session)

        await service.create_checkout(db_session, user=user, plan_name="plus")

        assert cap["price_id"] == "price_plus"

    async def test_a_user_with_no_email_still_checks_out(self, db_session, monkeypatch) -> None:
        # Stripe collects the email itself in that case — we must not block.
        _configure(monkeypatch)
        cap: dict = {}
        _fake_stripe(monkeypatch, cap)
        user = await _user(db_session, email=None)

        await service.create_checkout(db_session, user=user, plan_name="pro")

        assert cap["customer_email"] is None


class TestRefusals:
    async def test_not_configured_is_503(self, db_session, monkeypatch) -> None:
        _configure(monkeypatch, stripe_secret_key=None)
        user = await _user(db_session)

        with pytest.raises(AppError) as ei:
            await service.create_checkout(db_session, user=user, plan_name="pro")
        assert ei.value.status_code == 503

    async def test_a_plan_that_isnt_sold_is_400(self, db_session, monkeypatch) -> None:
        # The free fallback tier has no price id — it must never reach Stripe.
        _configure(monkeypatch)
        user = await _user(db_session)

        with pytest.raises(AppError) as ei:
            await service.create_checkout(db_session, user=user, plan_name="free")
        assert ei.value.status_code == 400

    async def test_an_unknown_plan_is_400(self, db_session, monkeypatch) -> None:
        _configure(monkeypatch)
        user = await _user(db_session)

        with pytest.raises(AppError) as ei:
            await service.create_checkout(db_session, user=user, plan_name="enterprise")
        assert ei.value.status_code == 400

    async def test_an_already_subscribed_user_is_refused(self, db_session, monkeypatch) -> None:
        # The double-billing guard. A second checkout would create a second
        # live Stripe subscription and both would charge monthly — the customer
        # pays twice and finds out on a bank statement.
        from app.db.models import Plan, Subscription

        _configure(monkeypatch)
        user = await _user(db_session)
        plan = Plan(name="plus", price_cents=999, currency="USD", interval="month")
        db_session.add(plan)
        await db_session.flush()
        db_session.add(Subscription(user_id=user.id, plan_id=plan.id, status="active"))
        await db_session.flush()

        with pytest.raises(AppError) as ei:
            await service.create_checkout(db_session, user=user, plan_name="pro")
        assert ei.value.status_code == 409
        assert "already" in str(ei.value.message).lower()

    async def test_a_canceled_subscription_does_not_block_checkout(
        self, db_session, monkeypatch
    ) -> None:
        # Lapsed is not subscribed: someone who cancelled must be able to
        # re-subscribe, or the guard locks out returning customers.
        from app.db.models import Plan, Subscription

        _configure(monkeypatch)
        user = await _user(db_session)
        plan = Plan(name="plus", price_cents=999, currency="USD", interval="month")
        db_session.add(plan)
        await db_session.flush()
        db_session.add(
            Subscription(user_id=user.id, plan_id=plan.id, status="canceled")
        )
        await db_session.flush()
        _fake_stripe(monkeypatch, {})

        out = await service.create_checkout(db_session, user=user, plan_name="pro")
        assert out["url"]

    async def test_a_stripe_error_becomes_502(self, db_session, monkeypatch) -> None:
        _configure(monkeypatch)
        user = await _user(db_session)

        async def boom(**kwargs):
            raise stripe_client.StripeError("card_declined", code="card_declined")

        monkeypatch.setattr(stripe_client, "create_checkout_session", boom)

        with pytest.raises(AppError) as ei:
            await service.create_checkout(db_session, user=user, plan_name="pro")
        assert ei.value.status_code == 502

    async def test_no_url_from_stripe_is_502(self, db_session, monkeypatch) -> None:
        _configure(monkeypatch)
        _fake_stripe(monkeypatch, {}, url=None)
        user = await _user(db_session)

        with pytest.raises(AppError) as ei:
            await service.create_checkout(db_session, user=user, plan_name="pro")
        assert ei.value.status_code == 502


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
class TestEndpoint:
    """The route itself: mounted, authenticated as the user (no admin
    permission), and returning the envelope. Stripe is mocked at the client."""

    def _app_client(self):
        from fastapi.testclient import TestClient

        from app.main import create_app

        return TestClient(create_app())

    async def _plain_user(self, email: str) -> None:
        from app.core.security import hash_password
        from app.db import base as db_base
        from app.db.models import User

        async with db_base.get_sessionmaker()() as db:
            db.add(
                User(
                    external_id=f"user:{email}",
                    email=email,
                    password_hash=hash_password("correct-horse-1"),
                    status="active",
                )
            )
            await db.commit()

    def test_unauthenticated_is_401(self) -> None:
        with self._app_client() as client:
            r = client.post("/api/v1/billing/checkout", json={"plan": "pro"})
        assert r.status_code == 401

    def test_a_signed_in_user_gets_a_url(self, monkeypatch) -> None:
        import asyncio
        import os

        from app.config import get_settings

        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_PRICE_IDS"] = '{"plus":"price_plus","pro":"price_pro"}'
        get_settings.cache_clear()

        async def fake(**kwargs):
            return {"id": "cs_1", "url": "https://checkout.stripe/go"}

        monkeypatch.setattr(stripe_client, "create_checkout_session", fake)
        try:
            with self._app_client() as client:
                asyncio.run(self._plain_user("buyer@example.com"))
                r = client.post(
                    "/api/v1/auth/login",
                    json={"email": "buyer@example.com", "password": "correct-horse-1"},
                )
                token = r.json()["data"]["access_token"]
                out = client.post(
                    "/api/v1/billing/checkout",
                    json={"plan": "pro"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert out.status_code == 200, out.text
            assert out.json()["data"]["url"] == "https://checkout.stripe/go"
        finally:
            os.environ.pop("STRIPE_SECRET_KEY", None)
            os.environ.pop("STRIPE_PRICE_IDS", None)
            get_settings.cache_clear()


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
class TestParamFlattening:
    def test_nested_params_become_bracket_keys(self) -> None:
        # Stripe's form encoding: this is what actually reaches the wire, and a
        # subtle bug here (e.g. list indices) fails only against real Stripe.
        flat = stripe_client._flatten(
            {
                "mode": "subscription",
                "line_items": [{"price": "p", "quantity": 1}],
                "metadata": {"user_id": "7"},
            }
        )
        assert flat["mode"] == "subscription"
        assert flat["line_items[0][price]"] == "p"
        assert flat["line_items[0][quantity]"] == "1"
        assert flat["metadata[user_id]"] == "7"

    def test_booleans_and_none(self) -> None:
        flat = stripe_client._flatten({"a": True, "b": False, "c": None})
        assert flat["a"] == "true"
        assert flat["b"] == "false"
        assert "c" not in flat, "None params are dropped, not sent as 'None'"
