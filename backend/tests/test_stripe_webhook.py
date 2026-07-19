"""The Stripe webhook: signature verification and event translation.

Phase 3. This is the piece that turns a completed payment into an active
subscription, so the two failure modes that matter are opposite: a forged or
replayed request must be rejected, and a genuine one must map to exactly the
right normalized event (a wrong user id or plan here silently subscribes the
wrong person, or nobody). No keys or network — the signing secret is ours to
choose in the test, and mapping is a pure function.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from app.modules.billing import stripe_webhook
from app.modules.billing.stripe_webhook import SignatureError

SECRET = "whsec_test_secret"


def _sign(payload: bytes, secret: str = SECRET, *, t: int | None = None) -> str:
    t = t if t is not None else int(time.time())
    signed = f"{t}.".encode() + payload
    v1 = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


class TestSignature:
    def test_a_valid_signature_passes(self) -> None:
        body = b'{"hello":"world"}'
        stripe_webhook.verify_signature(body, _sign(body), SECRET)  # no raise

    def test_a_tampered_body_fails(self) -> None:
        header = _sign(b'{"amount":100}')
        with pytest.raises(SignatureError):
            # Same signature, different body — exactly the forgery this stops.
            stripe_webhook.verify_signature(b'{"amount":999999}', header, SECRET)

    def test_the_wrong_secret_fails(self) -> None:
        body = b"{}"
        with pytest.raises(SignatureError):
            stripe_webhook.verify_signature(body, _sign(body, "whsec_other"), SECRET)

    def test_a_missing_header_fails(self) -> None:
        with pytest.raises(SignatureError):
            stripe_webhook.verify_signature(b"{}", None, SECRET)

    def test_a_malformed_header_fails(self) -> None:
        with pytest.raises(SignatureError):
            stripe_webhook.verify_signature(b"{}", "not-a-signature", SECRET)

    def test_a_stale_timestamp_is_rejected(self) -> None:
        # Replay defence: a captured-and-resent request an hour later.
        body = b"{}"
        old = _sign(body, t=int(time.time()) - 3600)
        with pytest.raises(SignatureError):
            stripe_webhook.verify_signature(body, old, SECRET)

    def test_raw_bytes_matter(self) -> None:
        # Re-serialized JSON has different bytes (spacing/key order); the
        # signature is over the exact wire bytes, so this must fail. It's the
        # reason the endpoint reads request.body() and not a parsed model.
        original = b'{"a":1,"b":2}'
        header = _sign(original)
        reserialized = json.dumps(json.loads(original), indent=2).encode()
        with pytest.raises(SignatureError):
            stripe_webhook.verify_signature(reserialized, header, SECRET)


def _event(kind: str, obj: dict) -> dict:
    return {"type": kind, "data": {"object": obj}}


class TestMapping:
    def test_checkout_completed_activates_the_subscription(self) -> None:
        events = stripe_webhook.to_events(
            _event(
                "checkout.session.completed",
                {
                    "client_reference_id": "7",
                    "metadata": {"user_id": "7", "plan": "pro"},
                    "subscription": "sub_123",
                },
            )
        )
        assert len(events) == 1
        e = events[0]
        assert e.event_type == "subscription.created"
        assert e.user_id == 7
        assert e.plan_name == "pro"
        assert e.provider_subscription_id == "sub_123"

    def test_checkout_without_metadata_is_ignored_not_crashed(self) -> None:
        # A checkout not started by us (no metadata) must not blow up the
        # endpoint — it's ignored so Stripe stops retrying.
        events = stripe_webhook.to_events(
            _event("checkout.session.completed", {"subscription": "sub_1"})
        )
        assert events == []

    def test_subscription_deleted_cancels(self) -> None:
        events = stripe_webhook.to_events(
            _event(
                "customer.subscription.deleted",
                {"id": "sub_123", "metadata": {"user_id": "7"}},
            )
        )
        assert events[0].event_type == "subscription.canceled"
        assert events[0].provider_subscription_id == "sub_123"

    def test_subscription_updated_to_past_due_maps(self) -> None:
        events = stripe_webhook.to_events(
            _event(
                "customer.subscription.updated",
                {"id": "sub_1", "status": "past_due", "metadata": {"user_id": "7"}},
            )
        )
        assert events[0].event_type == "subscription.past_due"

    def test_subscription_updated_to_active_is_noise(self) -> None:
        # updated fires constantly; only past_due is worth acting on.
        events = stripe_webhook.to_events(
            _event(
                "customer.subscription.updated",
                {"id": "sub_1", "status": "active", "metadata": {"user_id": "7"}},
            )
        )
        assert events == []

    def test_invoice_paid_records_a_payment(self) -> None:
        events = stripe_webhook.to_events(
            _event(
                "invoice.payment_succeeded",
                {
                    "id": "in_1",
                    "subscription": "sub_1",
                    "amount_paid": 1999,
                    "currency": "usd",
                    "subscription_details": {"metadata": {"user_id": "7"}},
                },
            )
        )
        assert events[0].event_type == "payment.succeeded"
        assert events[0].amount_cents == 1999
        assert events[0].currency == "USD"
        assert events[0].provider_payment_id == "in_1"

    def test_invoice_failed_records_a_failed_payment(self) -> None:
        events = stripe_webhook.to_events(
            _event(
                "invoice.payment_failed",
                {
                    "id": "in_2",
                    "amount_due": 999,
                    "currency": "usd",
                    "metadata": {"user_id": "7"},
                },
            )
        )
        assert events[0].event_type == "payment.failed"
        assert events[0].amount_cents == 999

    def test_an_unknown_event_is_ignored(self) -> None:
        # Stripe sends dozens of types we don't care about; each must be a no-op.
        assert stripe_webhook.to_events(_event("customer.created", {"id": "cus_1"})) == []
        assert stripe_webhook.to_events({"type": "ping"}) == []
