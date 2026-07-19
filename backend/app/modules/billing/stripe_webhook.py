"""Translate Stripe webhooks into the billing module's normalized events.

Two pure pieces, both testable without keys or network:

- :func:`verify_signature` — Stripe signs each webhook. The `Stripe-Signature`
  header is `t=<unix>,v1=<hex hmac>`; the signed payload is `"<t>.<raw body>"`,
  HMAC-SHA256 with the endpoint's signing secret (`whsec_…`). We recompute and
  compare in constant time, and reject a timestamp outside a tolerance so a
  captured request can't be replayed later. The RAW body matters — re-serialized
  JSON has different bytes and every signature would fail.

- :func:`to_events` — maps one Stripe event onto zero or more
  :class:`WebhookEvent`. Zero means "ack and ignore": Stripe sends dozens of
  event types and anything non-2xx makes it retry for days, so an unmapped event
  is a success, not an error. The user id and plan ride in the metadata we set
  at checkout (services/stripe_client.create_checkout_session).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from app.logging_conf import get_logger
from app.modules.billing.schemas import WebhookEvent

logger = get_logger(__name__)

#: Reject a signature whose timestamp is older/newer than this many seconds.
#: Stripe's own default. Bounds replay without tripping on ordinary clock skew.
_TOLERANCE_S = 300


class SignatureError(Exception):
    """The Stripe-Signature header is missing, malformed, stale, or wrong."""


def verify_signature(payload: bytes, sig_header: str | None, secret: str) -> None:
    """Raise :class:`SignatureError` unless ``sig_header`` validly signs ``payload``.

    ``payload`` is the raw request body (bytes), not parsed JSON.
    """
    if not sig_header:
        raise SignatureError("Missing Stripe-Signature header.")

    parts = dict(
        p.split("=", 1) for p in sig_header.split(",") if "=" in p
    )
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        raise SignatureError("Malformed Stripe-Signature header.")

    try:
        age = abs(time.time() - int(timestamp))
    except ValueError as e:
        raise SignatureError("Bad timestamp in Stripe-Signature.") from e
    if age > _TOLERANCE_S:
        raise SignatureError("Stripe-Signature timestamp outside tolerance.")

    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("Stripe signature mismatch.")


def _obj(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("data", {}).get("object", {}) or {}


def _user_id(meta: dict[str, Any]) -> int | None:
    raw = meta.get("user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def to_events(event: dict[str, Any]) -> list[WebhookEvent]:
    """Map a Stripe event to normalized events. ``[]`` means ignore it.

    Only the money-path events are mapped:

    - ``checkout.session.completed`` — the subscription is paid and live. This is
      the activation event; it carries client_reference_id + metadata.
    - ``customer.subscription.deleted`` — canceled.
    - ``customer.subscription.updated`` — only acted on when the status became
      ``past_due`` (a failed renewal); other updates are noise.
    - ``invoice.payment_succeeded`` / ``invoice.payment_failed`` — renewals and
      their failures, recorded as payments.
    """
    kind = event.get("type", "")
    obj = _obj(event)

    if kind == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        uid = _user_id(meta) or _user_id(
            {"user_id": obj.get("client_reference_id")}
        )
        plan = meta.get("plan")
        sub_id = obj.get("subscription")
        if uid is None or not plan or not sub_id:
            logger.warning("stripe.checkout_completed_missing_fields", have=list(meta))
            return []
        return [
            WebhookEvent(
                event_type="subscription.created",
                user_id=uid,
                plan_name=plan,
                provider_subscription_id=str(sub_id),
            )
        ]

    if kind == "customer.subscription.deleted":
        uid = _user_id(obj.get("metadata") or {})
        if uid is None:
            return []
        return [
            WebhookEvent(
                event_type="subscription.canceled",
                user_id=uid,
                provider_subscription_id=str(obj.get("id")) if obj.get("id") else None,
            )
        ]

    if kind == "customer.subscription.updated":
        if obj.get("status") != "past_due":
            return []
        uid = _user_id(obj.get("metadata") or {})
        if uid is None:
            return []
        return [
            WebhookEvent(
                event_type="subscription.past_due",
                user_id=uid,
                provider_subscription_id=str(obj.get("id")) if obj.get("id") else None,
            )
        ]

    if kind in ("invoice.payment_succeeded", "invoice.payment_failed"):
        meta = obj.get("subscription_details", {}).get("metadata") or obj.get("metadata") or {}
        uid = _user_id(meta)
        if uid is None:
            logger.warning("stripe.invoice_without_user", invoice=obj.get("id"))
            return []
        succeeded = kind == "invoice.payment_succeeded"
        amount = obj.get("amount_paid") if succeeded else obj.get("amount_due")
        return [
            WebhookEvent(
                event_type="payment.succeeded" if succeeded else "payment.failed",
                user_id=uid,
                provider_subscription_id=str(obj.get("subscription"))
                if obj.get("subscription")
                else None,
                provider_payment_id=str(obj.get("id")) if obj.get("id") else None,
                amount_cents=int(amount) if amount is not None else 0,
                currency=(obj.get("currency") or "usd").upper(),
            )
        ]

    return []
