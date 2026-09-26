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


def _global(settings) -> dict:
    """The USD price list — the only one the website's cards show. A regional
    list (the India tiers, INR) is offered by the app to its region and never
    appears beside the USD cards."""
    return {n: p for n, p in settings.plan_catalog.items() if settings.plan_region(n) is None}


def test_every_sold_plan_gets_a_card(settings) -> None:
    html = _cards(settings)
    monthly = [n for n in _global(settings) if not n.endswith("_yearly")]
    for name in monthly:
        assert f'>{settings.plan_title(name)}</div>' in html
    # Yearly plans are the SAME card under a toggle, never a card of their own —
    # eight cards would ask the visitor to compare a price with itself.
    assert html.count('class="plan-name"') == len(monthly)


def test_the_card_price_is_the_catalog_price(settings) -> None:
    html = _cards(settings)
    for name, plan in _global(settings).items():
        if name.endswith("_yearly") or plan["period"] == "trial":
            continue
        monthly = f'data-m="{pricing._money(float(plan["price_usd"]))}"'
        yearly_plan = settings.plan_catalog[f"{name}_yearly"]
        annual = f'data-a="{pricing._money(float(yearly_plan["price_usd"]))}"'
        assert f"{monthly} {annual}" in html, f"{name} priced wrong on the site"


def test_the_annual_note_is_the_yearly_price_divided_by_twelve(settings) -> None:
    html = _cards(settings)
    notes = re.findall(r"works out at (?:<[^>]+>)?\$([0-9.]+)(?:</span>)?/mo", html)
    expected = [
        f"{float(p['price_usd']) / 12:.2f}"
        for n, p in _global(settings).items()
        if n.endswith("_yearly")
    ]
    assert notes == expected


def test_allowances_come_from_the_catalog(settings) -> None:
    html = _cards(settings)
    for name, plan in _global(settings).items():
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


def test_a_yearly_card_says_what_the_year_saves(settings) -> None:
    """Ten months' money for twelve: the card says so, in dollars and percent."""
    html = _cards(settings)
    assert 'data-usd="16.00">$16</span></span>' in html  # Plus: 12 × 8 − 80
    assert html.count('class="plan-save"') == len(
        [n for n in _global(settings) if n.endswith("_yearly")]
    )


def test_a_card_whose_year_saves_nothing_says_nothing(settings) -> None:
    clone = settings.model_copy(deep=True)
    clone.plan_catalog = {k: dict(v) for k, v in clone.plan_catalog.items()}
    clone.plan_catalog["lite_yearly"]["price_usd"] = 72.0  # twelve months' money
    html = pricing.plan_cards_html(clone)
    lite = html[html.index("Lite") : html.index("Plus")]
    assert "plan-save" not in lite
    assert html.count('class="plan-save"') == 2


def test_render_leaves_no_placeholder_behind(settings) -> None:
    from pathlib import Path

    import app.web.router as web_router

    page = pricing.render(
        Path(web_router._INDEX).read_text(encoding="utf-8"), settings
    )
    for marker in (
        "<!--PLAN_CARDS-->",
        "<!--PLAN_CARDS_IN-->",
        "<!--TRIAL_MINUTES-->",
    ):
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


# ── the India price list (shown instead of the USD one to a visitor in India) ──


def test_the_india_cards_are_rupees_for_a_period_and_start_hidden(settings) -> None:
    html = pricing.india_cards_html(settings)
    assert html.startswith('<div class="plans plans-in" data-region="IN" hidden')
    assert html.count('class="plan-name"') == 3
    assert ">साथी<" in html and ">Plus<" in html and ">Pro<" in html
    # native rupee amounts the currency picker must not convert
    assert html.count('class="plan-amount native"') == 3
    assert 'data-m="299" data-a="3,300"' in html
    assert 'data-m="599" data-a="5,500"' in html
    assert 'data-m="999" data-a="11,000"' in html
    assert pricing._rupees(1100000) == "11,00,000" and pricing._rupees(999) == "999"
    assert 'data-m="for 30 days" data-a="for 12 months"' in html
    assert "works out at ₹275/mo" in html and "works out at ₹917/mo" in html
    # what the year saves, on the card: 12 × 299 − 3,300 = 288, Plus 1,688 — the
    # amount alone, no percentage (Faraz, 2026-09-20)
    assert "Save ₹288<" in html and "Save ₹1,688<" in html and "Save ₹988<" in html
    assert "%)" not in html
    assert "One-time payment · no auto-renew · GST included" in html
    assert "$" not in html and "per month" not in html
    assert html.count("Most popular") == 1  # Plus, as on the USD list


def test_the_page_carries_both_lists_and_the_region_line(settings) -> None:
    from pathlib import Path

    import app.web.router as web_router

    page = pricing.render(Path(web_router._INDEX).read_text(encoding="utf-8"), settings)
    assert page.count('class="plans"') == 1 and page.count('class="plans plans-in"') == 1
    # The saving lives on each yearly card now, not on the toggle.
    assert 'class="save-b"' not in page
    assert "data-region-line" in page and "function regionApply" in page
    # the line lives under the currency picker, above the cards — and the
    # "₹ INR" chip is what opens the India list (one choice, not two)
    assert page.index("data-region-line") < page.index('class="plans"')
    assert "var REGION_OF={INR:'IN',AED:'AE'}" in page and "regionSet" not in page
    assert ".plan-amount:not(.native)" in page, "the picker must leave rupee amounts alone"


def test_every_usd_yearly_card_says_what_it_saves(settings) -> None:
    html = _cards(settings)
    for name, plan in _global(settings).items():
        if name.endswith("_yearly") or plan["period"] == "trial":
            continue
        monthly = float(plan["price_usd"])
        annual = float(settings.plan_catalog[f"{name}_yearly"]["price_usd"])
        saved = monthly * 12 - annual
        assert f'data-usd="{saved:.2f}">${pricing._money(saved)}</span></span>' in html, name


def test_paid_cards_have_no_button_and_the_page_says_where_to_buy(settings) -> None:
    """A plan is bought inside the app; only the free card keeps its download."""
    html = _cards(settings)
    assert html.count("plan-btn") == 1 and "Start free" in html
    assert "Choose " not in html
    assert "plan-btn" not in pricing.india_cards_html(settings)
    assert "plan-btn" not in pricing.uae_cards_html(settings)
    from pathlib import Path

    import app.web.router as web_router

    page = pricing.render(Path(web_router._INDEX).read_text(encoding="utf-8"), settings)
    assert "Plans are bought inside the FarryOn app" in page


def test_no_page_promises_a_trial_by_days(settings) -> None:
    """2026-09-26: the CTA banner and the About page said "14-day free
    trial" while the free tier is talk-minutes with no end date. Faraz: drop
    it; the banner keeps only the shipping promise, now 7 to 10 business
    days (English and Hindi)."""
    from pathlib import Path

    import app.web.router as web_router
    from app.web import contact, i18n

    for path in (web_router._INDEX, web_router._ABOUT):
        page = pricing.render(
            contact.render(Path(path).read_text(encoding="utf-8"), settings),
            settings,
        )
        assert not re.search(r"\b\d+[- ]day free trial", page, re.I)
        hindi = i18n.localize(page, "hi", path_en="/about", site="https://x")
        assert "दिन का फ्री ट्रायल" not in hindi
    home = pricing.render(
        contact.render(Path(web_router._INDEX).read_text(encoding="utf-8"), settings),
        settings,
    )
    assert "Ships to UAE and GCC in 7 to 10 business days." in home
    assert "5 business days" not in home
    hindi = i18n.localize(home, "hi", path_en="/", site="https://x")
    assert "UAE और GCC में 7 से 10 कार्यदिवसों में डिलीवरी।" in hindi
