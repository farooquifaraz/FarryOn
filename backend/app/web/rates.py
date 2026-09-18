"""Live exchange rates for the pricing page: USD, AED and INR.

The app plans are priced in USD (Stripe bills in USD) and the glasses in AED
(they are sold over WhatsApp, in Dubai). A visitor in Mumbai wants to know
what that is in rupees before they write to anyone — so the page carries a
currency picker, and the numbers behind it come from here.

Rates are fetched from a free, keyless source, cached for twelve hours in the
process, and embedded into the page as one small JSON block at render time:
no third-party script on the page (the CSP stays ``script-src 'self'``), no
extra request from the browser, and no flash of one currency turning into
another. When every source is down the last good rates are served, marked
``stale``; when there has never been a good fetch, the built-in fallback is
served, marked ``fallback`` — the page then says "approximate".

The conversion itself happens in the browser (index.html, ``fxApply``): the
server never rounds, so the page can show the exact whole-number conversion
and say which rate it used.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import time
from html import escape
from typing import Any

from app.logging_conf import get_logger

logger = get_logger(__name__)

#: The currencies the page offers, base first.
CURRENCIES: tuple[str, ...] = ("USD", "AED", "INR")

#: What is served before the first successful fetch, and after every source
#: has failed with nothing good cached. AED is a hard peg (3.6725 since 1997);
#: INR moves, so this is only ever a stand-in and the page says so.
FALLBACK_RATES: dict[str, float] = {"USD": 1.0, "AED": 3.6725, "INR": 95.96}
FALLBACK_AS_OF = "2026-09-18"

#: How long a good fetch is trusted, and how long a failed one is not retried
#: — a dead source must not be hit on every page view.
TTL_S = 12 * 3600
RETRY_S = 10 * 60
FETCH_TIMEOUT_S = 5.0


def _parse_er_api(payload: dict[str, Any]) -> tuple[dict[str, float], str]:
    if payload.get("result") != "success":
        raise ValueError("er-api: result != success")
    rates = payload["rates"]
    as_of = _dt.datetime.fromtimestamp(
        int(payload["time_last_update_unix"]), tz=_dt.timezone.utc
    ).date().isoformat()
    return {c: float(rates[c]) for c in CURRENCIES if c != "USD"}, as_of


def _parse_frankfurter(payload: dict[str, Any]) -> tuple[dict[str, float], str]:
    rates = payload["rates"]
    return {c: float(rates[c]) for c in CURRENCIES if c != "USD"}, str(payload["date"])


#: (name, url, parser) — tried in order; the first that answers wins.
SOURCES: tuple[tuple[str, str, Any], ...] = (
    ("open.er-api.com", "https://open.er-api.com/v6/latest/USD", _parse_er_api),
    (
        "frankfurter.dev",
        "https://api.frankfurter.dev/v1/latest?base=USD&symbols=AED,INR",
        _parse_frankfurter,
    ),
)


def _snapshot(rates: dict[str, float], as_of: str, source: str) -> dict[str, Any]:
    full = {"USD": 1.0}
    full.update({c: round(float(rates[c]), 4) for c in CURRENCIES if c != "USD"})
    return {"base": "USD", "rates": full, "asOf": as_of, "source": source}


class _Cache:
    def __init__(self) -> None:
        self.good: dict[str, Any] | None = None  # the last successful fetch
        self.fetched_at = 0.0
        self.retry_at = 0.0
        self.lock = asyncio.Lock()


_cache = _Cache()


async def _fetch_once() -> dict[str, Any] | None:
    import httpx

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
        for name, url, parse in SOURCES:
            try:
                response = await client.get(url)
                response.raise_for_status()
                rates, as_of = parse(response.json())
                if any(rates[c] <= 0 for c in rates):
                    raise ValueError("non-positive rate")
                return _snapshot(rates, as_of, name)
            except Exception as exc:  # noqa: BLE001 - try the next source
                logger.warning("rates.source_failed", source=name, error=repr(exc))
    return None


async def current() -> dict[str, Any]:
    """The rates to embed: fresh, else last good (``stale``), else fallback."""
    now = time.monotonic()
    fresh = _cache.good is not None and now - _cache.fetched_at < TTL_S
    if not fresh and now >= _cache.retry_at:
        async with _cache.lock:
            # A second page view that queued on the lock finds the first one's
            # result and does not fetch again.
            if _cache.good is None or time.monotonic() - _cache.fetched_at >= TTL_S:
                got = await _fetch_once()
                if got is not None:
                    _cache.good = got
                    _cache.fetched_at = time.monotonic()
                    logger.info(
                        "rates.refreshed",
                        source=got["source"],
                        as_of=got["asOf"],
                        aed=got["rates"]["AED"],
                        inr=got["rates"]["INR"],
                    )
                else:
                    _cache.retry_at = time.monotonic() + RETRY_S
    if _cache.good is not None:
        if time.monotonic() - _cache.fetched_at < TTL_S:
            return _cache.good
        return {**_cache.good, "source": "stale"}
    return _snapshot(FALLBACK_RATES, FALLBACK_AS_OF, "fallback")


def rates_html(data: dict[str, Any]) -> str:
    """The JSON block the page's script reads (``#fx-rates``)."""
    body = json.dumps(data, separators=(",", ":"))
    # "</script>" inside JSON would end the block early; escape() covers it.
    return f'<script id="fx-rates" type="application/json">{escape(body, quote=False)}</script>'


def render(html: str, data: dict[str, Any]) -> str:
    """Fill the landing page's rates placeholder."""
    return html.replace("<!--PRICING_RATES-->", rates_html(data))
