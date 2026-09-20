"""The glasses shop on the landing page: prices, Buy / Add-to-cart buttons
and the catalog the cart script reads.

The cart itself is browser-side (index.html: a drawer kept in localStorage)
and the money moves through ``modules/shop`` — ``POST /api/v1/shop/checkout``
turns the cart into a Stripe Checkout Session with shipping-address
collection. This module only renders: every price on the page comes from
:data:`app.web.products.PRICES_AED`, so the card, the cart and the charge
can never disagree.
"""

from __future__ import annotations

import json
from html import escape

from app.config import Settings
from app.web.products import COLOURS, MODELS, PRICES_AED, Gallery, galleries

# ISO country -> name, for the cart's country picker and "Deliver to".
# The picker lists all of these — the buyer can pick anywhere — and the page
# says "we don't deliver there yet" for one outside SHOP_SHIP_COUNTRIES.
_COUNTRY_NAMES = {
    "AE": "United Arab Emirates", "SA": "Saudi Arabia", "QA": "Qatar", "OM": "Oman", "BH": "Bahrain",
    "KW": "Kuwait", "IN": "India", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka", "NP": "Nepal",
    "EG": "Egypt", "JO": "Jordan", "LB": "Lebanon", "IQ": "Iraq", "TR": "Türkiye", "MA": "Morocco",
    "GB": "United Kingdom", "IE": "Ireland", "US": "United States", "CA": "Canada", "AU": "Australia",
    "NZ": "New Zealand", "SG": "Singapore", "MY": "Malaysia", "ID": "Indonesia", "PH": "Philippines",
    "TH": "Thailand", "VN": "Vietnam", "JP": "Japan", "KR": "South Korea", "CN": "China", "HK": "Hong Kong",
    "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy", "NL": "Netherlands", "BE": "Belgium",
    "CH": "Switzerland", "AT": "Austria", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "PL": "Poland", "PT": "Portugal", "GR": "Greece", "CZ": "Czechia", "RO": "Romania", "HU": "Hungary",
    "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya", "GH": "Ghana", "ET": "Ethiopia", "TZ": "Tanzania",
    "BR": "Brazil", "MX": "Mexico", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
}

# The seven emirates: when the country is the UAE the "State / Emirate" field
# is a picker, not free text — the courier needs it spelled one way.
EMIRATES = ["Abu Dhabi", "Dubai", "Sharjah", "Ajman", "Umm Al Quwain", "Ras Al Khaimah", "Fujairah"]

# Where the buyer probably is, from the clock — the country picker's first
# guess. They can change it; nothing is decided by it.
_COUNTRY_ZONES = {
    "Asia/Dubai": "AE", "Asia/Riyadh": "SA", "Asia/Qatar": "QA", "Asia/Muscat": "OM", "Asia/Bahrain": "BH",
    "Asia/Kuwait": "KW", "Asia/Kolkata": "IN", "Asia/Calcutta": "IN", "Asia/Karachi": "PK", "Asia/Dhaka": "BD",
    "Asia/Colombo": "LK", "Asia/Kathmandu": "NP", "Africa/Cairo": "EG", "Asia/Amman": "JO", "Asia/Beirut": "LB",
    "Asia/Baghdad": "IQ", "Europe/Istanbul": "TR", "Africa/Casablanca": "MA", "Europe/London": "GB",
    "Europe/Dublin": "IE", "America/New_York": "US", "America/Chicago": "US", "America/Denver": "US",
    "America/Los_Angeles": "US", "America/Phoenix": "US", "America/Toronto": "CA", "America/Vancouver": "CA",
    "Australia/Sydney": "AU", "Australia/Melbourne": "AU", "Australia/Perth": "AU", "Pacific/Auckland": "NZ",
    "Asia/Singapore": "SG", "Asia/Kuala_Lumpur": "MY", "Asia/Jakarta": "ID", "Asia/Manila": "PH",
    "Asia/Bangkok": "TH", "Asia/Ho_Chi_Minh": "VN", "Asia/Tokyo": "JP", "Asia/Seoul": "KR", "Asia/Shanghai": "CN",
    "Asia/Hong_Kong": "HK", "Europe/Berlin": "DE", "Europe/Paris": "FR", "Europe/Madrid": "ES", "Europe/Rome": "IT",
    "Europe/Amsterdam": "NL", "Europe/Brussels": "BE", "Europe/Zurich": "CH", "Europe/Vienna": "AT",
    "Europe/Stockholm": "SE", "Europe/Oslo": "NO", "Europe/Copenhagen": "DK", "Europe/Helsinki": "FI",
    "Europe/Warsaw": "PL", "Europe/Lisbon": "PT", "Europe/Athens": "GR", "Europe/Prague": "CZ",
    "Europe/Bucharest": "RO", "Europe/Budapest": "HU", "Africa/Johannesburg": "ZA", "Africa/Lagos": "NG",
    "Africa/Nairobi": "KE", "Africa/Accra": "GH", "Africa/Addis_Ababa": "ET", "Africa/Dar_es_Salaam": "TZ",
    "America/Sao_Paulo": "BR", "America/Mexico_City": "MX", "America/Argentina/Buenos_Aires": "AR",
    "America/Santiago": "CL", "America/Bogota": "CO",
}


def out_of_stock(settings: Settings) -> set[str]:
    """``{"l802", "gs5:Red"}`` from the env setting — model slugs and
    ``slug:Colour`` variants that are not for sale right now. The admin
    panel's toggles (shop_stock table) are unioned in by
    :func:`app.modules.shop.service.sold_out_keys`; callers that have a DB
    pass that set in, callers that don't get the env list alone."""
    return {str(x).strip() for x in getattr(settings, "shop_out_of_stock", []) if str(x).strip()}


def stock_keys() -> list[dict]:
    """Every toggleable thing, in card order: each model, then its colours."""
    out = []
    for slug, name in MODELS.items():
        if slug not in PRICES_AED:
            continue
        out.append({"key": slug, "model": name, "colour": None})
        for c in COLOURS.get(slug, []):
            out.append({"key": f"{slug}:{c}", "model": name, "colour": c})
    return out


def model_in_stock(sold_out: set[str], slug: str) -> bool:
    return slug not in sold_out


def colour_in_stock(sold_out: set[str], slug: str, colour: str) -> bool:
    return f"{slug}:{colour}" not in sold_out


def thumb_url(gallery: Gallery | None) -> str | None:
    """The 400px rendition of the model's first still, for the cart row."""
    if not gallery or not gallery.stills:
        return None
    _shot, _alt, renditions = gallery.stills[0]
    # keys are extensions with the dot (".webp": {400: "front-400.webp", …});
    # the original sits under width 0
    for ext in (".webp", ".jpg", ".jpeg", ".png"):
        widths = renditions.get(ext) or {}
        name = widths.get(400) or widths.get(0)
        if name:
            return f"/media/{gallery.slug}/{name}"
    return None


def catalog(settings: Settings, sold_out: set[str] | None = None) -> dict:
    """What the cart script needs: names, prices, colours and their stock,
    a thumbnail when there is photography, where we deliver."""
    sold_out = out_of_stock(settings) if sold_out is None else sold_out
    found = galleries(settings)
    items = {}
    for slug, name in MODELS.items():
        if slug not in PRICES_AED:
            continue
        items[slug] = {
            "name": name,
            "price_aed": PRICES_AED[slug],
            "in_stock": model_in_stock(sold_out, slug),
            "colours": [
                {"name": c, "in_stock": colour_in_stock(sold_out, slug, c)}
                for c in COLOURS.get(slug, [])
            ],
            "thumb": thumb_url(found.get(slug)),
        }
    ship_to = [c.upper() for c in getattr(settings, "shop_ship_countries", ["AE"])]
    names = dict(_COUNTRY_NAMES)
    for c in ship_to:
        names.setdefault(c, c)
    return {
        "items": items,
        "ship_to": ship_to,
        "ship_to_names": [names.get(c, c) for c in ship_to],
        # every country the picker offers, shippable ones first, then A–Z
        "countries": [[c, names[c]] for c in ship_to]
        + sorted(([c, n] for c, n in names.items() if c not in ship_to), key=lambda x: x[1]),
        "country_zones": _COUNTRY_ZONES,
        "emirates": EMIRATES,
        "enabled": bool(getattr(settings, "stripe_secret_key", None)),
    }


def buy_row(sold_out: set[str], slug: str) -> str:
    """Buy now / Add to cart under a spec card, with a colour picker where
    the model has colours. A sold-out model gets one disabled "Out of stock"
    button (the WhatsApp button under it still works); a sold-out colour is
    greyed out in the picker. Text nodes are plain English so the Hindi page
    translates them."""
    if slug not in PRICES_AED:
        return ""
    if not model_in_stock(sold_out, slug):
        return (
            f'<div class="buy-row" data-buy="{slug}" data-stock="out">'
            '<button type="button" class="buy-btn" disabled>Out of stock</button></div>'
        )
    colours = COLOURS.get(slug, [])
    picker = ""
    if colours:
        opts = "".join(
            f'<option value="{escape(c)}">{escape(c)}</option>'
            if colour_in_stock(sold_out, slug, c)
            else f'<option value="{escape(c)}" disabled>{escape(c)} — out of stock</option>'
            for c in colours
        )
        picker = (
            f'<label class="buy-colour"><span>Colour</span>'
            f'<select data-colour="{slug}" aria-label="Colour">{opts}</select></label>'
        )
    return (
        f'<div class="buy-row" data-buy="{slug}">{picker}'
        f'<button type="button" class="buy-btn solid" onclick="cartBuy(\'{slug}\')">Buy now</button>'
        f'<button type="button" class="buy-btn" onclick="cartAdd(\'{slug}\')">Add to cart</button>'
        "</div>"
    )


def render(html: str, settings: Settings, sold_out: set[str] | None = None) -> str:
    """Fill the price and buy-row slots on the spec cards and the catalog
    block the cart script reads. ``sold_out`` is the union of the env list
    and the admin toggles (service.sold_out_keys); without it, env only."""
    sold_out = out_of_stock(settings) if sold_out is None else sold_out
    for slug, price in PRICES_AED.items():
        html = html.replace(f"<!--PRICE_AED:{slug}-->", str(price))
    for slug in MODELS:
        html = html.replace(f"<!--BUY_ROW:{slug}-->", buy_row(sold_out, slug))
    block = (
        '<script id="shop-catalog" type="application/json">'
        + json.dumps(catalog(settings, sold_out), ensure_ascii=False).replace("</", "<\\/")
        + "</script>"
    )
    return html.replace("<!--SHOP_CATALOG-->", block)
