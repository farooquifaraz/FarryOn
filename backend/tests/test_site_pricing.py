"""The landing page quotes the plan catalog, not a second copy of it.

The page once advertised a "free 60-minute trial" while the catalog sold 30,
and a yearly price that had moved on without it. These tests exist so a price
change in ``Settings.plan_catalog`` cannot leave the marketing site behind.
"""

from __future__ import annotations

import re

import pytest

from app.config import get_settings
from app.web import pricing


@pytest.fixture()
def settings():
    return get_settings()


def _cards(settings) -> str:
    return pricing.plan_cards_html(settings)


def test_every_sold_plan_gets_a_card(settings) -> None:
    html = _cards(settings)
    monthly = [n for n in settings.plan_catalog if not n.endswith("_yearly")]
    for name in monthly:
        assert f'>{settings.plan_title(name)}</div>' in html
    # Yearly plans are the SAME card under a toggle, never a card of their own —
    # eight cards would ask the visitor to compare a price with itself.
    assert html.count('class="plan-name"') == len(monthly)


def test_the_card_price_is_the_catalog_price(settings) -> None:
    html = _cards(settings)
    for name, plan in settings.plan_catalog.items():
        if name.endswith("_yearly") or plan["period"] == "trial":
            continue
        monthly = f'data-m="{pricing._money(float(plan["price_usd"]))}"'
        yearly_plan = settings.plan_catalog[f"{name}_yearly"]
        annual = f'data-a="{pricing._money(float(yearly_plan["price_usd"]))}"'
        assert f"{monthly} {annual}" in html, f"{name} priced wrong on the site"


def test_the_annual_note_is_the_yearly_price_divided_by_twelve(settings) -> None:
    html = _cards(settings)
    notes = re.findall(r"works out at \$([0-9.]+)/mo", html)
    expected = [
        f"{float(p['price_usd']) / 12:.2f}"
        for n, p in settings.plan_catalog.items()
        if n.endswith("_yearly")
    ]
    assert notes == expected


def test_allowances_come_from_the_catalog(settings) -> None:
    html = _cards(settings)
    for name, plan in settings.plan_catalog.items():
        if name.endswith("_yearly"):
            continue
        minutes = int(plan["talk_minutes"])
        if plan["period"] == "trial":
            assert f"{minutes:,} minutes of talk time (one-time)" in html
            # A trial refills never; the page must not imply a monthly reset.
            assert f"{minutes:,} talk minutes a month" not in html
        else:
            assert f"{minutes:,} talk minutes a month" in html
            assert f"{int(plan['image_scans']):,} image scans a month" in html


def test_the_headline_promises_the_trial_that_exists(settings) -> None:
    assert pricing.trial_minutes(settings) == int(
        settings.plan_catalog["free"]["talk_minutes"]
    )


def test_the_saving_badge_counts_the_months_actually_free(settings) -> None:
    """Every tier is ten months' money, so the badge says two months free."""
    months = {
        round(12 - float(p["price_usd"]) / float(settings.plan_catalog[n[:-7]]["price_usd"]))
        for n, p in settings.plan_catalog.items()
        if n.endswith("_yearly")
    }
    assert len(months) == 1, "tiers disagree — the badge must not name one number"
    free = months.pop()
    assert pricing.annual_saving_label(settings) == f"{free} months free"


def test_an_uneven_discount_is_described_as_up_to(settings) -> None:
    """If one tier is ever a better bargain, no single figure may stand for all."""
    clone = settings.model_copy(deep=True)
    clone.plan_catalog = {
        k: dict(v) for k, v in clone.plan_catalog.items()
    }
    clone.plan_catalog["lite_yearly"]["price_usd"] = 66.0  # 11 months, not 10
    assert pricing.annual_saving_label(clone).startswith("save up to ")


def test_render_leaves_no_placeholder_behind(settings) -> None:
    from pathlib import Path

    import app.web.router as web_router

    page = pricing.render(
        Path(web_router._INDEX).read_text(encoding="utf-8"), settings
    )
    for marker in ("<!--PLAN_CARDS-->", "<!--TRIAL_MINUTES-->", "<!--ANNUAL_SAVING-->"):
        assert marker not in page
    assert 'class="plans"' in page


def test_a_new_catalog_plan_needs_no_html_edit(settings) -> None:
    """The point of the exercise: adding a plan is a config change."""
    clone = settings.model_copy(deep=True)
    clone.plan_catalog = dict(clone.plan_catalog)
    clone.plan_catalog["max"] = {
        "price_usd": 40.0,
        "period": "month",
        "talk_minutes": 2000,
        "image_scans": 2000,
        "web_searches": 2000,
    }
    clone.plan_catalog["max_yearly"] = {
        "price_usd": 440.0,
        "period": "year",
        "talk_minutes": 2000,
        "image_scans": 2000,
        "web_searches": 2000,
    }
    html = pricing.plan_cards_html(clone)
    assert 'data-m="40" data-a="440"' in html
    assert "2,000 talk minutes a month" in html
