"""The Stripe webhook end to end: a signed checkout.session.completed turns
into a live subscription row, and a redelivery doesn't double it.

This is the seam where money becomes access, so it's tested through the real
endpoint (signature, raw body, DB write) rather than just the mapping function.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import base as db_base
from app.db.models import Plan, Subscription, User
from app.main import create_app

SECRET = "whsec_endpoint_test"


def setup_module() -> None:
    os.environ["STRIPE_WEBHOOK_SECRET"] = SECRET
    get_settings.cache_clear()


def teardown_module() -> None:
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    get_settings.cache_clear()


def _sign(payload: bytes, *, t: int | None = None) -> str:
    t = t if t is not None else int(time.time())
    v1 = hmac.new(SECRET.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


async def _seed_user_and_plan() -> int:
    async with db_base.get_sessionmaker()() as db:
        user = User(external_id="wh-user", email="wh@example.com", display_name="WH")
        db.add(user)
        db.add(Plan(name="pro", price_cents=1999, currency="USD", interval="month"))
        await db.flush()
        uid = user.id
        await db.commit()
    return uid


def _checkout_completed(uid: int, sub_id: str = "sub_e2e") -> bytes:
    return json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(uid),
                    "metadata": {"user_id": str(uid), "plan": "pro"},
                    "subscription": sub_id,
                }
            },
        }
    ).encode()


async def _subs_for(uid: int) -> list[Subscription]:
    async with db_base.get_sessionmaker()() as db:
        return list(
            (
                await db.execute(select(Subscription).where(Subscription.user_id == uid))
            ).scalars()
        )


def test_a_signed_checkout_creates_an_active_subscription() -> None:
    with TestClient(create_app()) as client:
        uid = asyncio.run(_seed_user_and_plan())
        body = _checkout_completed(uid)

        r = client.post(
            "/api/v1/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": _sign(body), "content-type": "application/json"},
        )
        assert r.status_code == 200, r.text

        subs = asyncio.run(_subs_for(uid))
        assert len(subs) == 1
        assert subs[0].status == "active"
        assert subs[0].provider == "stripe"
        assert subs[0].provider_subscription_id == "sub_e2e"


def test_a_redelivered_event_does_not_double_the_subscription() -> None:
    # Stripe redelivers. The second identical event must be a no-op, not a
    # second active subscription for the same paid one.
    with TestClient(create_app()) as client:
        uid = asyncio.run(_seed_user_and_plan())
        body = _checkout_completed(uid)
        headers = {"Stripe-Signature": _sign(body), "content-type": "application/json"}

        client.post("/api/v1/webhooks/stripe", content=body, headers=headers)
        r2 = client.post("/api/v1/webhooks/stripe", content=body, headers=headers)
        assert r2.status_code == 200

        assert len(asyncio.run(_subs_for(uid))) == 1


def test_a_forged_signature_is_401_and_writes_nothing() -> None:
    with TestClient(create_app()) as client:
        uid = asyncio.run(_seed_user_and_plan())
        body = _checkout_completed(uid)

        r = client.post(
            "/api/v1/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": "t=1,v1=deadbeef", "content-type": "application/json"},
        )
        assert r.status_code == 401
        assert asyncio.run(_subs_for(uid)) == []


def test_an_unmapped_event_is_acked() -> None:
    with TestClient(create_app()) as client:
        body = json.dumps({"type": "customer.created", "data": {"object": {"id": "cus_1"}}}).encode()
        r = client.post(
            "/api/v1/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": _sign(body), "content-type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["ignored"] == "customer.created"
