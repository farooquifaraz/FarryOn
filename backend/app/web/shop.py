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

# ISO country -> what the cart says under "Deliver to".
_COUNTRY_NAMES = {
    "AE": "United Arab Emirates",
    "IN": "India",
    "SA": "Saudi Arabia",
    "QA": "Qatar",
    "OM": "Oman",
    "BH": "Bahrain",
    "KW": "Kuwait",
    "GB": "United Kingdom",
    "US": "United States",
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
    ship_to = list(getattr(settings, "shop_ship_countries", ["AE"]))
    return {
        "items": items,
        "ship_to": ship_to,
        "ship_to_names": [_COUNTRY_NAMES.get(c, c) for c in ship_to],
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
