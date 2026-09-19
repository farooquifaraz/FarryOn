"""A thin Stripe REST client over httpx.

Deliberately not the `stripe` SDK: this project already speaks async httpx
(see services/vision.py), the surface we need is tiny, and a form-encoded POST
is less machinery than a sync SDK bridged into an async server. Stripe's API is
form-encoded with bearer auth (the secret key) and nested params in bracket
notation (`line_items[0][price]=…`).

Everything here raises :class:`StripeError` on a non-2xx so callers can turn it
into one clean AppError rather than leaking httpx exceptions.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.logging_conf import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.stripe.com"
_TIMEOUT = httpx.Timeout(15.0)


class StripeError(Exception):
    """A Stripe request failed. Carries Stripe's own error code when present."""

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


def _flatten(data: dict[str, Any], parent: str = "") -> dict[str, str]:
    """Stripe wants nested params as bracketed keys, form-encoded.

    ``{"line_items": [{"price": "p", "quantity": 1}]}`` becomes
    ``{"line_items[0][price]": "p", "line_items[0][quantity]": "1"}``. Booleans
    go to Stripe's ``"true"``/``"false"``; None values are dropped.
    """
    out: dict[str, str] = {}
    for key, value in data.items():
        full = f"{parent}[{key}]" if parent else key
        if value is None:
            continue
        if isinstance(value, dict):
            out.update(_flatten(value, full))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                idx = f"{full}[{i}]"
                if isinstance(item, dict):
                    out.update(_flatten(item, idx))
                else:
                    out[idx] = _scalar(item)
        else:
            out[full] = _scalar(value)
    return out


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


async def create_checkout_session(
    *,
    secret_key: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    client_reference_id: str,
    customer_email: str | None,
    metadata: dict[str, str],
    client: httpx.AsyncClient | None = None,
    mode: str = "subscription",
) -> dict[str, Any]:
    """Create a Checkout Session and return Stripe's response.

    ``mode`` is ``"subscription"`` (renewing, the USD plans) or ``"payment"``
    (a single charge for the period, the India plans). In payment mode the
    metadata rides on the PaymentIntent instead of a subscription, and the
    completed-session event names ``payment_intent`` rather than
    ``subscription`` — the webhook reads whichever is there.

    ``client_reference_id`` and ``metadata`` are how the webhook (phase 3) maps
    the resulting subscription back to our user — both are echoed on the
    ``checkout.session.completed`` event. ``metadata`` is also copied onto the
    subscription itself via ``subscription_data`` so later subscription events
    (renewals, cancellations) carry it too, not just the one-off session.
    """
    if mode not in ("subscription", "payment"):
        raise ValueError(f"unsupported checkout mode: {mode}")
    carrier = (
        {"subscription_data": {"metadata": metadata}}
        if mode == "subscription"
        else {"payment_intent_data": {"metadata": metadata}}
    )
    params = _flatten(
        {
            "mode": mode,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": client_reference_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            "metadata": metadata,
            **carrier,
            # Email prefilled but editable; omitted when we don't know it so
            # Stripe collects it rather than sending an empty field.
            **({"customer_email": customer_email} if customer_email else {}),
        }
    )
    return await _post(
        "/v1/checkout/sessions", params, secret_key=secret_key, client=client
    )


async def _post(
    path: str,
    params: dict[str, str],
    *,
    secret_key: str,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    owns = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        res = await client.post(
            f"{_API_BASE}{path}",
            data=params,
            headers={"Authorization": f"Bearer {secret_key}"},
        )
    except httpx.HTTPError as e:
        raise StripeError(f"Could not reach Stripe: {e}") from e
    finally:
        if owns:
            await client.aclose()

    try:
        body = res.json()
    except ValueError:
        body = {}
    if res.status_code >= 400:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        msg = err.get("message", f"Stripe returned {res.status_code}")
        logger.warning("stripe.error", status=res.status_code, code=err.get("code"))
        raise StripeError(msg, code=err.get("code"), status=res.status_code)
    return body
