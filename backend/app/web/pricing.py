"""The marketing site's pricing cards, rendered from the plan catalog.

The landing page used to spell its prices and allowances out in HTML. That made
``Settings.plan_catalog`` the source of truth for the app, the API and the
database — and the website a fourth, silent copy that drifted: it was still
advertising a "free 60-minute trial" long after the trial became 30 minutes.

So the numbers come from one place now. This module owns only the *copy* a
catalog has no opinion about (a tagline, the non-numeric bullets, the button
label); every price, minute and scan on the page is read from the catalog at
render time. Add a plan there and it appears here, priced correctly, without
anyone editing HTML.
"""

from __future__ import annotations

from html import escape

from app.config import Settings

# Yearly plans are named "<monthly>_yearly" — the annual toggle pairs a card
# with its yearly twin through this suffix.
_YEARLY_SUFFIX = "_yearly"

# Per-plan wording. Numbers are NEVER in here: `extra` holds only the bullets
# that no cap can express. A plan missing from this map still renders — it just
# gets its allowances and nothing else, which is the safe way to fail.
_COPY: dict[str, dict[str, object]] = {
    "free": {
        "desc": "A real taste of Farry — every feature unlocked.",
        "extra": ["All features unlocked", "No credit card needed"],
        "cta": "Start free",
    },
    "lite": {
        "desc": "For everyday helpers.",
        "extra": ["Notes, reminders &amp; email", "WhatsApp &amp; Telegram"],
    },
    "plus": {
        "desc": "For daily power users.",
        "extra": ["Everything in Lite", "Priority responses"],
        "popular": True,
    },
    "pro": {
        "desc": "For heavy, all-day use.",
        "extra": ["Everything in Plus", "Priority support"],
    },
}


def _rupees(amount: float) -> str:
    """Whole rupees with Indian grouping: 3300 -> "3,300", 1100000 -> "11,00,000"."""
    n = round(amount)
    digits = str(abs(n))
    if len(digits) <= 3:
        out = digits
    else:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        out = ",".join(groups) + "," + tail
    return f"-{out}" if n < 0 else out


def _money(amount: float) -> str:
    """`6.0` -> "6", `5.42` -> "5.42" — a trailing ".00" reads like a typo."""
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def _card(
    settings: Settings, name: str, plan: dict, yearly: dict | None, delay: int
) -> str:
    copy = _COPY.get(name, {})
    minutes = int(plan.get("talk_minutes", 0))
    scans = int(plan.get("image_scans", 0))
    trial = str(plan.get("period")) == "trial"
    monthly_price = float(plan.get("price_usd", 0.0))

    feats = [f"{minutes:,} talk minutes a month", f"{scans:,} image scans a month"]
    if trial:
        # A trial is a one-time pot, not a monthly allowance — saying "a month"
        # here would promise a refill that never comes.
        feats = [f"{minutes:,} minutes of talk time (one-time)"]
    feats += [str(x) for x in copy.get("extra", [])]

    if trial:
        amount = (
            f'<div class="plan-amount"><sup>$</sup>'
            f'<span class="pv" data-m="0" data-a="0">0</span></div>'
            f'<div class="plan-period">one-time trial</div>'
        )
    else:
        annual = float(yearly["price_usd"]) if yearly else monthly_price * 12
        per_month = annual / 12
        # What the year saves against twelve months, said on the card itself
        # — the only reason to pick yearly, so it sits next to the price.
        saved = monthly_price * 12 - annual
        saving = (
            f'<span class="ann" style="display:none"> · '
            f'<span class="plan-save">Save '
            f'<span class="fx-usd" data-usd="{saved:.2f}">${_money(saved)}</span>'
            "</span></span>"
            if saved > 0
            else ""
        )
        amount = (
            f'<div class="plan-amount"><sup>$</sup>'
            f'<span class="pv" data-m="{_money(monthly_price)}" '
            f'data-a="{_money(annual)}">{_money(monthly_price)}</span></div>'
            f'<div class="plan-period">'
            f'<span class="per" data-m="per month" data-a="per year">per month</span>'
            f'<span class="ann" style="display:none"> · works out at '
            f'<span class="fx-usd" data-usd="{per_month:.2f}">${per_month:.2f}</span>'
            f"/mo</span>{saving}</div>"
        )

    popular = bool(copy.get("popular"))
    classes = "plan popular reveal" if popular else "plan reveal"
    if delay:
        classes += f" d{delay}"
    badge = '<div class="plan-pop">Most popular</div>' if popular else ""
    bullets = "".join(f"<li>{f}</li>" for f in feats)
    # Plans are bought inside the app (Settings → Subscription), so a paid
    # card carries no button — a "Choose Plus" that only downloaded the APK
    # promised a checkout the page cannot give (Faraz, 2026-09-20). The free
    # tier keeps its button: that one really is the download.
    button = (
        f'<a href="/download/arm64" class="plan-btn outline">'
        f'{escape(str(copy.get("cta", "Start free")))}</a>'
        if trial
        else ""
    )

    return (
        f'<div class="{classes}">{badge}'
        f'<div class="plan-inner">'
        f'<div class="plan-name">{escape(settings.plan_title(name))}</div>'
        f'<p class="plan-desc">{copy.get("desc", "")}</p>'
        f"<div>{amount}</div>"
        f'<hr class="plan-div">'
        f'<ul class="plan-feats">{bullets}</ul>'
        f"{button}"
        f"</div></div>"
    )


# The India tiers' copy. Numbers, again, never live here.
_COPY_IN: dict[str, dict[str, object]] = {
    "sathi_in": {
        "desc": "For everyday helpers.",
        "extra": ["Notes, reminders &amp; email", "WhatsApp &amp; Telegram"],
    },
    "plus_in": {
        "desc": "For daily power users.",
        "extra": ["Everything in साथी", "Priority responses"],
        "popular": True,
    },
    "pro_in": {
        "desc": "For heavy, all-day use.",
        "extra": ["Everything in Plus", "Priority support"],
    },
}


_COPY_AE: dict[str, dict[str, object]] = {
    "lite_ae": {
        "desc": "For everyday helpers.",
        "extra": ["Notes, reminders &amp; email", "WhatsApp &amp; Telegram"],
    },
    "plus_ae": {
        "desc": "For daily power users.",
        "extra": ["Everything in Lite", "Priority responses"],
        "popular": True,
    },
    "pro_ae": {
        "desc": "For heavy, all-day use.",
        "extra": ["Everything in Plus", "Priority support"],
    },
}


def _aed(amount: float) -> str:
    """25 → "25", 14.1667 → "14.17": whole dirhams when there are no fils."""
    return f"{amount:,.2f}".removesuffix(".00")


# region -> (grid class, card copy, sign, number formatter, billing line)
_REGIONAL: dict[str, tuple[str, dict, str, object, str]] = {
    "IN": ("plans-in", _COPY_IN, "₹", _rupees, "One-time payment · no auto-renew · GST included"),
    "AE": ("plans-ae", _COPY_AE, "AED", _aed, "Cancel anytime · VAT included"),
}


def _regional_card(
    settings: Settings, name: str, plan: dict, yearly: dict | None, delay: int, region: str
) -> str:
    """One regional tier: the India tiers in rupees, bought outright for 30
    days or 12 months; the UAE tiers in dirhams, renewing monthly or yearly.

    The amount is marked ``native`` so the currency picker leaves it alone —
    these prices ARE the price, not a conversion. A one-time plan's period
    words say "for 30 days" rather than "per month", because nothing renews.
    """
    _grid, copy_all, sign, fmt, billing = _REGIONAL[region]
    copy = copy_all.get(name, {})
    minutes = int(plan.get("talk_minutes", 0))
    scans = int(plan.get("image_scans", 0))
    monthly = settings.plan_price_cents(name) / 100
    annual = settings.plan_price_cents(name + _YEARLY_SUFFIX) / 100 if yearly else monthly * 12
    per_month = annual / 12
    saved = monthly * 12 - annual
    one_time = settings.plan_is_one_time(name)
    per_m, per_a = ("for 30 days", "for 12 months") if one_time else ("per month", "per year")
    sep = "" if len(sign) == 1 else " "  # ₹275 but AED 14.17
    feats = [
        f"{minutes:,} talk minutes a month",
        f"{scans:,} image scans a month",
        billing,
    ]
    feats += [str(x) for x in copy.get("extra", [])]
    amount = (
        f'<div class="plan-amount native"><sup class="code">{sign}</sup>'
        f'<span class="pv" data-m="{fmt(monthly)}" data-a="{fmt(annual)}">'
        f"{fmt(monthly)}</span></div>"
        '<div class="plan-period">'
        f'<span class="per" data-m="{per_m}" data-a="{per_a}">{per_m}</span>'
        f'<span class="ann" style="display:none"> · works out at {sign}{sep}'
        f"{fmt(round(per_month)) if region == 'IN' else fmt(per_month)}/mo</span>"
        + (
            f'<span class="ann" style="display:none"> · <span class="plan-save">'
            f"Save {sign}{sep}{fmt(saved)}</span></span>"
            if saved > 0
            else ""
        )
        + "</div>"
    )
    popular = bool(copy.get("popular"))
    classes = "plan popular reveal" if popular else "plan reveal"
    if delay:
        classes += f" d{delay}"
    badge = '<div class="plan-pop">Most popular</div>' if popular else ""
    bullets = "".join(f"<li>{f}</li>" for f in feats)
    # No button: a plan is bought inside the app (see _card).
    return (
        f'<div class="{classes}">{badge}'
        f'<div class="plan-inner">'
        f'<div class="plan-name">{escape(settings.plan_title(name))}</div>'
        f'<p class="plan-desc">{copy.get("desc", "")}</p>'
        f"<div>{amount}</div>"
        f'<hr class="plan-div">'
        f'<ul class="plan-feats">{bullets}</ul>'
        "</div></div>"
    )


def regional_cards_html(settings: Settings, region: str) -> str:
    """A regional price list, hidden until the page decides the visitor is
    there (their currency choice, which their timezone seeds) or they ask
    for it. Empty when the catalog has no tiers for the region, so the slot
    then costs nothing."""
    grid = _REGIONAL[region][0]
    catalog = settings.plan_catalog
    monthly = [
        (name, plan)
        for name, plan in catalog.items()
        if not name.endswith(_YEARLY_SUFFIX) and settings.plan_region(name) == region
    ]
    if not monthly:
        return ""
    cards = "".join(
        _regional_card(settings, name, plan, catalog.get(name + _YEARLY_SUFFIX), i, region)
        for i, (name, plan) in enumerate(monthly)
    )
    return (
        f'<div class="plans {grid}" data-region="{region}" hidden '
        f'style="grid-template-columns:repeat({len(monthly)},1fr)">{cards}</div>'
    )


def india_cards_html(settings: Settings) -> str:
    """The India price list (₹, bought for a period)."""
    return regional_cards_html(settings, "IN")


def uae_cards_html(settings: Settings) -> str:
    """The UAE price list (AED, renewing)."""
    return regional_cards_html(settings, "AE")


def plan_cards_html(settings: Settings) -> str:
    """The `<div class="plans">` grid for every monthly/trial plan on offer."""
    catalog = settings.plan_catalog
    # The global (USD) list only: a regional price list (India) is shown to
    # that region by the app, and by the site's currency picker in a later
    # step — never side by side with the USD cards.
    monthly = [
        (name, plan)
        for name, plan in catalog.items()
        if not name.endswith(_YEARLY_SUFFIX) and settings.plan_region(name) is None
    ]
    cards = "".join(
        _card(settings, name, plan, catalog.get(name + _YEARLY_SUFFIX), i)
        for i, (name, plan) in enumerate(monthly)
    )
    columns = len(monthly)
    return (
        f'<div class="plans" style="grid-template-columns:repeat({columns},1fr)">'
        f"{cards}</div>"
    )


def trial_minutes(settings: Settings) -> int:
    """Talk minutes in the free tier — the number the headline promises."""
    plan = settings.plan_catalog.get("free", {})
    return int(plan.get("talk_minutes", 0))


def render(html: str, settings: Settings) -> str:
    """Fill the landing page's pricing placeholders."""
    return (
        html.replace("<!--PLAN_CARDS-->", plan_cards_html(settings))
        .replace("<!--PLAN_CARDS_IN-->", india_cards_html(settings))
        .replace("<!--PLAN_CARDS_AE-->", uae_cards_html(settings))
        .replace("<!--TRIAL_MINUTES-->", str(trial_minutes(settings)))
    )
