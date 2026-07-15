"""Billing: plan CRUD, subscription list, revenue summary/MRR math,
transactions, and the webhook receiver (secret gate + idempotency)."""

from __future__ import annotations

import asyncio
import os

from fastapi.testclient import TestClient

from app.config import get_settings
from app.core.security import hash_password
from app.db import base as db_base
from app.db.models import User, UserRole
from app.db.seed import seed_roles_and_permissions
from app.main import create_app

PASSWORD = "correct-horse-1"
WEBHOOK_SECRET = "test-webhook-secret"


def _client() -> TestClient:
    return TestClient(create_app())


async def _seed_user_with_role(email: str, role_name: str) -> None:
    sessionmaker = db_base.get_sessionmaker()
    async with sessionmaker() as db:
        roles = await seed_roles_and_permissions(db)
        user = User(
            external_id=f"user:{email}",
            email=email,
            password_hash=hash_password(PASSWORD),
            status="active",
        )
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=roles[role_name].id))
        await db.commit()


def _login(client: TestClient, email: str) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _webhook_headers() -> dict:
    return {"X-Webhook-Secret": WEBHOOK_SECRET}


def _setup_admin(client: TestClient, email: str) -> str:
    asyncio.run(_seed_user_with_role(email, "super_admin"))
    return _login(client, email)


def _register_user(client: TestClient, email: str) -> int:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    return client.get("/api/v1/me", headers=_auth(_login(client, email))).json()["data"]["id"]


def _make_plan(client: TestClient, admin_token: str, name: str, price_cents: int, interval: str = "month") -> dict:
    r = client.post(
        "/api/v1/admin/plans",
        headers=_auth(admin_token),
        json={"name": name, "price_cents": price_cents, "interval": interval},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _subscribe(client: TestClient, user_id: int, plan_name: str, sub_id: str) -> None:
    r = client.post(
        "/api/v1/webhooks/billing/stripe",
        headers=_webhook_headers(),
        json={
            "event_type": "subscription.created",
            "user_id": user_id,
            "plan_name": plan_name,
            "provider_subscription_id": sub_id,
        },
    )
    assert r.status_code == 200, r.text


def _pay(client: TestClient, user_id: int, amount_cents: int, payment_id: str) -> dict:
    r = client.post(
        "/api/v1/webhooks/billing/stripe",
        headers=_webhook_headers(),
        json={
            "event_type": "payment.succeeded",
            "user_id": user_id,
            "amount_cents": amount_cents,
            "provider_payment_id": payment_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def setup_module() -> None:
    os.environ["BILLING_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    get_settings.cache_clear()


def teardown_module() -> None:
    os.environ.pop("BILLING_WEBHOOK_SECRET", None)
    get_settings.cache_clear()


def test_plans_require_permission() -> None:
    with _client() as client:
        r = client.get("/api/v1/admin/plans")
        assert r.status_code == 401

        asyncio.run(_seed_user_with_role("plain@example.com", "user"))
        token = _login(client, "plain@example.com")
        r = client.get("/api/v1/admin/plans", headers=_auth(token))
        assert r.status_code == 403


def test_plan_crud() -> None:
    with _client() as client:
        admin_token = _setup_admin(client, "root@example.com")

        plan = _make_plan(client, admin_token, "pro", 999)
        assert plan["price_cents"] == 999
        assert plan["interval"] == "month"

        # Duplicate name rejected.
        r = client.post(
            "/api/v1/admin/plans",
            headers=_auth(admin_token),
            json={"name": "pro", "price_cents": 1},
        )
        assert r.status_code == 409

        r = client.patch(
            f"/api/v1/admin/plans/{plan['id']}",
            headers=_auth(admin_token),
            json={"price_cents": 1299, "features": ["glasses sync", "translation"]},
        )
        assert r.status_code == 200
        assert r.json()["data"]["price_cents"] == 1299
        assert r.json()["data"]["features"] == ["glasses sync", "translation"]


def test_webhook_requires_secret() -> None:
    with _client() as client:
        _setup_admin(client, "root2@example.com")
        user_id = _register_user(client, "u@example.com")

        r = client.post(
            "/api/v1/webhooks/billing/stripe",
            json={"event_type": "payment.succeeded", "user_id": user_id, "amount_cents": 100},
        )
        assert r.status_code == 401

        r = client.post(
            "/api/v1/webhooks/billing/stripe",
            headers={"X-Webhook-Secret": "wrong"},
            json={"event_type": "payment.succeeded", "user_id": user_id, "amount_cents": 100},
        )
        assert r.status_code == 401


def test_subscription_lifecycle_via_webhooks() -> None:
    with _client() as client:
        admin_token = _setup_admin(client, "root3@example.com")
        _make_plan(client, admin_token, "premium", 1900)
        user_id = _register_user(client, "sub@example.com")

        _subscribe(client, user_id, "premium", "sub_abc")

        rows = client.get(
            "/api/v1/admin/subscriptions", headers=_auth(admin_token)
        ).json()["data"]
        assert len(rows) == 1
        assert rows[0]["status"] == "active"
        assert rows[0]["plan_name"] == "premium"
        assert rows[0]["user_email"] == "sub@example.com"

        # past_due, then canceled.
        client.post(
            "/api/v1/webhooks/billing/stripe",
            headers=_webhook_headers(),
            json={
                "event_type": "subscription.past_due",
                "user_id": user_id,
                "provider_subscription_id": "sub_abc",
            },
        )
        rows = client.get(
            "/api/v1/admin/subscriptions?status=past_due", headers=_auth(admin_token)
        ).json()["data"]
        assert len(rows) == 1

        client.post(
            "/api/v1/webhooks/billing/stripe",
            headers=_webhook_headers(),
            json={
                "event_type": "subscription.canceled",
                "user_id": user_id,
                "provider_subscription_id": "sub_abc",
            },
        )
        rows = client.get(
            "/api/v1/admin/subscriptions?status=canceled", headers=_auth(admin_token)
        ).json()["data"]
        assert len(rows) == 1


def test_revenue_summary_and_mrr_math() -> None:
    with _client() as client:
        admin_token = _setup_admin(client, "root4@example.com")
        _make_plan(client, admin_token, "pro", 1000)  # $10/mo
        _make_plan(client, admin_token, "premium-yearly", 12000, interval="year")  # $120/yr → $10/mo

        u1 = _register_user(client, "a@example.com")
        u2 = _register_user(client, "b@example.com")
        _subscribe(client, u1, "pro", "sub_1")
        _subscribe(client, u2, "premium-yearly", "sub_2")

        _pay(client, u1, 1000, "pay_1")
        _pay(client, u2, 12000, "pay_2")
        _pay(client, u1, 1000, "pay_3")

        summary = client.get(
            "/api/v1/admin/revenue/summary", headers=_auth(admin_token)
        ).json()["data"]

        assert summary["total_revenue_cents"] == 14000
        # MRR: pro $10 + yearly $120/12 = $10 → 2000 cents
        assert summary["mrr_cents"] == 2000
        assert summary["active_subscribers"] == 2
        plans = {row["plan"]: row for row in summary["revenue_by_plan"]}
        assert plans["pro"]["mrr_cents"] == 1000
        assert plans["premium-yearly"]["mrr_cents"] == 1000
        assert len(summary["revenue_over_time"]) == 1  # all payments this month


def test_refund_reduces_nothing_but_flips_status() -> None:
    with _client() as client:
        admin_token = _setup_admin(client, "root5@example.com")
        _make_plan(client, admin_token, "pro", 1000)
        user_id = _register_user(client, "r@example.com")
        _subscribe(client, user_id, "pro", "sub_r")
        _pay(client, user_id, 1000, "pay_r")

        r = client.post(
            "/api/v1/webhooks/billing/stripe",
            headers=_webhook_headers(),
            json={
                "event_type": "payment.refunded",
                "user_id": user_id,
                "provider_payment_id": "pay_r",
            },
        )
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "refunded"

        summary = client.get(
            "/api/v1/admin/revenue/summary", headers=_auth(admin_token)
        ).json()["data"]
        assert summary["total_revenue_cents"] == 0  # refunded payment excluded

        txns = client.get(
            "/api/v1/admin/revenue/transactions?status=refunded", headers=_auth(admin_token)
        ).json()["data"]
        assert len(txns) == 1


def test_webhook_payment_idempotency() -> None:
    with _client() as client:
        admin_token = _setup_admin(client, "root6@example.com")
        _make_plan(client, admin_token, "pro", 1000)
        user_id = _register_user(client, "dup@example.com")
        _subscribe(client, user_id, "pro", "sub_d")

        first = _pay(client, user_id, 1000, "pay_dup")
        second = _pay(client, user_id, 1000, "pay_dup")  # redelivered event

        assert second.get("duplicate") is True
        assert second["payment_id"] == first["payment_id"]

        summary = client.get(
            "/api/v1/admin/revenue/summary", headers=_auth(admin_token)
        ).json()["data"]
        assert summary["total_revenue_cents"] == 1000  # counted once


def test_transactions_pagination_meta() -> None:
    with _client() as client:
        admin_token = _setup_admin(client, "root7@example.com")
        _make_plan(client, admin_token, "pro", 500)
        user_id = _register_user(client, "many@example.com")
        _subscribe(client, user_id, "pro", "sub_m")
        for i in range(3):
            _pay(client, user_id, 500, f"pay_m{i}")

        r = client.get(
            "/api/v1/admin/revenue/transactions?page=1&page_size=2",
            headers=_auth(admin_token),
        )
        body = r.json()
        assert len(body["data"]) == 2
        assert body["meta"]["total"] == 3
