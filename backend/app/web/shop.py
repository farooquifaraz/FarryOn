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
from app.web.products import COLOURS, MODELS, PRICES_AED


def catalog(settings: Settings) -> dict:
    """What the cart script needs: names, prices, colours, where we ship."""
    return {
        "items": {
            slug: {"name": name, "price_aed": PRICES_AED[slug], "colours": COLOURS.get(slug, [])}
            for slug, name in MODELS.items()
            if slug in PRICES_AED
        },
        "ship_to": list(getattr(settings, "shop_ship_countries", ["AE"])),
        "enabled": bool(getattr(settings, "stripe_secret_key", None)),
    }


def buy_row(slug: str) -> str:
    """Buy now / Add to cart under a spec card, with a colour picker where
    the model has colours. Both buttons carry the slug; the script does the
    rest. Text nodes are plain English so the Hindi page translates them."""
    if slug not in PRICES_AED:
        return ""
    colours = COLOURS.get(slug, [])
    picker = ""
    if colours:
        opts = "".join(f'<option value="{escape(c)}">{escape(c)}</option>' for c in colours)
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


def render(html: str, settings: Settings) -> str:
    """Fill the price and buy-row slots on the spec cards and the catalog
    block the cart script reads."""
    for slug, price in PRICES_AED.items():
        html = html.replace(f"<!--PRICE_AED:{slug}-->", str(price))
    for slug in MODELS:
        html = html.replace(f"<!--BUY_ROW:{slug}-->", buy_row(slug))
    block = (
        '<script id="shop-catalog" type="application/json">'
        + json.dumps(catalog(settings), ensure_ascii=False).replace("</", "<\\/")
        + "</script>"
    )
    return html.replace("<!--SHOP_CATALOG-->", block)
