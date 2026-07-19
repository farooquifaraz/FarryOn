"""Pydantic schemas for /api/v1/admin/plans, /admin/subscriptions,
/admin/revenue/* and the billing webhook."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SubscriptionStatus = Literal["trialing", "active", "past_due", "canceled", "expired"]
PaymentStatus = Literal["succeeded", "failed", "refunded"]


class CheckoutRequest(BaseModel):
    """The plan a signed-in user wants to subscribe to. Validated against the
    sold plans (stripe_price_ids) in the service, not here — the set of sellable
    plans is config, not a fixed literal."""

    plan: str = Field(min_length=2, max_length=64)


class PlanCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    price_cents: int = Field(ge=0)
    currency: str = Field(default="USD", max_length=8)
    interval: Literal["month", "year"] = "month"
    description: str | None = Field(default=None, max_length=255)
    features: list[str] = Field(default_factory=list)


class PlanUpdateRequest(BaseModel):
    price_cents: int | None = Field(default=None, ge=0)
    description: str | None = None
    features: list[str] | None = None
    is_active: bool | None = None


class PlanOut(BaseModel):
    id: int
    name: str
    price_cents: int
    currency: str
    interval: str
    description: str | None
    features: list[str]
    is_active: bool


class SubscriptionOut(BaseModel):
    id: int
    user_id: int
    user_email: str | None
    user_display_name: str | None
    plan_name: str
    status: str
    started_at: datetime
    current_period_end: datetime | None
    lifetime_paid_cents: int


class WebhookEvent(BaseModel):
    """Normalized provider-agnostic webhook payload.

    Real providers (Stripe/Razorpay/Play Billing) each have their own event
    shape; when integrating one, add a translation layer in the router that
    maps its events onto this shape — service logic stays unchanged.
    """

    event_type: Literal[
        "subscription.created",
        "subscription.renewed",
        "subscription.canceled",
        "subscription.past_due",
        "payment.succeeded",
        "payment.failed",
        "payment.refunded",
    ]
    user_id: int
    plan_name: str | None = None
    provider_subscription_id: str | None = None
    provider_payment_id: str | None = None
    amount_cents: int | None = Field(default=None, ge=0)
    currency: str = "USD"
    period_end: datetime | None = None
