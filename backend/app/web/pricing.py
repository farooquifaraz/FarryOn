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
        "cta": "Choose Lite",
    },
    "plus": {
        "desc": "For daily power users.",
        "extra": ["Everything in Lite", "Priority responses"],
        "cta": "Choose Plus",
        "popular": True,
    },
    "pro": {
        "desc": "For heavy, all-day use.",
        "extra": ["Everything in Plus", "Priority support"],
        "cta": "Choose Pro",
    },
}


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
        amount = (
            f'<div class="plan-amount"><sup>$</sup>'
            f'<span class="pv" data-m="{_money(monthly_price)}" '
            f'data-a="{_money(annual)}">{_money(monthly_price)}</span></div>'
            f'<div class="plan-period">'
            f'<span class="per" data-m="per month" data-a="per year">per month</span>'
            f'<span class="ann" style="display:none"> · works out at '
            f'${per_month:.2f}/mo</span></div>'
        )

    popular = bool(copy.get("popular"))
    classes = "plan popular reveal" if popular else "plan reveal"
    if delay:
        classes += f" d{delay}"
    badge = '<div class="plan-pop">Most popular</div>' if popular else ""
    btn = "solid" if popular else "outline"
    bullets = "".join(f"<li>{f}</li>" for f in feats)
    cta = escape(str(copy.get("cta", f"Choose {settings.plan_title(name)}")))

    return (
        f'<div class="{classes}">{badge}'
        f'<div class="plan-inner">'
        f'<div class="plan-name">{escape(settings.plan_title(name))}</div>'
        f'<p class="plan-desc">{copy.get("desc", "")}</p>'
        f"<div>{amount}</div>"
        f'<hr class="plan-div">'
        f'<ul class="plan-feats">{bullets}</ul>'
        f'<a href="/download/arm64" class="plan-btn {btn}">{cta}</a>'
        f"</div></div>"
    )


def plan_cards_html(settings: Settings) -> str:
    """The `<div class="plans">` grid for every monthly/trial plan on offer."""
    catalog = settings.plan_catalog
    monthly = [
        (name, plan)
        for name, plan in catalog.items()
        if not name.endswith(_YEARLY_SUFFIX)
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


def annual_saving_label(settings: Settings) -> str:
    """"2 months free", or "save up to 17%" if the tiers ever disagree.

    Derived, because the badge used to read "~1 month free" and would have gone
    on saying so however the yearly prices moved. Months are the honest unit
    while every tier offers the same deal; the moment one tier is a better
    bargain than another, a single percentage would overstate the rest, so the
    label says "up to" instead.
    """
    savings: list[float] = []
    for name, plan in settings.plan_catalog.items():
        if not name.endswith(_YEARLY_SUFFIX):
            continue
        base = settings.plan_catalog.get(name[: -len(_YEARLY_SUFFIX)])
        if not base:
            continue
        monthly = float(base.get("price_usd", 0.0))
        if monthly <= 0:
            continue
        savings.append(12 - float(plan.get("price_usd", 0.0)) / monthly)
    if not savings:
        return "yearly billing"
    months = savings[0]
    if all(abs(x - months) < 0.01 for x in savings) and abs(months - round(months)) < 0.01:
        whole = round(months)
        return f"{whole} month{'s' if whole != 1 else ''} free"
    best = max(savings) / 12
    return f"save up to {round(best * 100)}%"


def render(html: str, settings: Settings) -> str:
    """Fill the landing page's pricing placeholders."""
    return (
        html.replace("<!--PLAN_CARDS-->", plan_cards_html(settings))
        .replace("<!--TRIAL_MINUTES-->", str(trial_minutes(settings)))
        .replace("<!--ANNUAL_SAVING-->", annual_saving_label(settings))
    )
