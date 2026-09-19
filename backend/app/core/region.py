"""Which regional price list a request should see.

Plans can be region-specific (the India plans, priced in INR). The region a
request belongs to is decided — per Faraz, 2026-09-20 — by where the person
is right now, not by their SIM or card:

1. ``X-Region`` — the app's own verdict when it has one (``IN``).
2. ``X-Timezone`` — the device's IANA zone; India's zones mean ``IN``.
3. ``CF-IPCountry`` — set by Cloudflare when the site ever sits behind it;
   ignored today because nothing sets it (the backend sits behind Nginx
   Proxy Manager and Caddy, neither of which does GeoIP).

Anything else is the global (USD) price list, spelled ``None``. This is a
DISPLAY decision — which plans are offered — and it deliberately does not
try to be a lock: an Indian traveller sees global prices, and a VPN sees
Indian ones. The lock (card country at payment) is a separate, later step.
"""

from __future__ import annotations

from collections.abc import Mapping

INDIA = "IN"

_INDIA_ZONES = frozenset({"Asia/Kolkata", "Asia/Calcutta"})
_KNOWN = frozenset({INDIA})


def region_from_headers(headers: Mapping[str, str]) -> str | None:
    """``"IN"`` for an Indian request, ``None`` for the global price list."""
    explicit = (headers.get("x-region") or "").strip().upper()
    if explicit in _KNOWN:
        return explicit
    tz = (headers.get("x-timezone") or "").strip()
    if tz in _INDIA_ZONES:
        return INDIA
    country = (headers.get("cf-ipcountry") or "").strip().upper()
    if country in _KNOWN:
        return country
    return None
