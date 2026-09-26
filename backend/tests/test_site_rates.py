"""The currency picker's rates: fetched, cached, embedded, and never a blank.

The page must render with sensible rates whatever the internet is doing:
fresh from a source when one answers, the last good set when none does, the
built-in fallback before the first success — and the JSON must say which,
because the page words its note differently for each.
"""

from __future__ import annotations

import json
import re

import pytest

from app.web import rates

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _cold_cache(monkeypatch):
    monkeypatch.setattr(rates, "_cache", rates._Cache())
    yield


def _stub_fetch(monkeypatch, result):
    calls = {"n": 0}

    async def fake():
        calls["n"] += 1
        return result

    monkeypatch.setattr(rates, "_fetch_once", fake)
    return calls


async def test_a_good_fetch_is_embedded_and_cached(monkeypatch) -> None:
    calls = _stub_fetch(
        monkeypatch, rates._snapshot({"AED": 3.6725, "INR": 95.96}, "2026-09-18", "open.er-api.com")
    )
    first = await rates.current()
    second = await rates.current()
    assert first["source"] == "open.er-api.com" and first["rates"]["USD"] == 1.0
    assert first["rates"]["INR"] == 95.96 and first["asOf"] == "2026-09-18"
    assert second is first
    assert calls["n"] == 1, "twelve hours of page views cost one fetch"


async def test_no_source_at_all_serves_the_fallback_and_says_so(monkeypatch) -> None:
    calls = _stub_fetch(monkeypatch, None)
    data = await rates.current()
    assert data["source"] == "fallback"
    assert data["rates"]["AED"] == 3.6725 and data["rates"]["INR"] > 0
    await rates.current()
    assert calls["n"] == 1, "a dead source is not hit again on the next page view"


async def test_after_a_good_fetch_an_outage_serves_the_last_good_rates_as_stale(monkeypatch) -> None:
    _stub_fetch(monkeypatch, rates._snapshot({"AED": 3.6725, "INR": 90.0}, "2026-09-10", "frankfurter.dev"))
    good = await rates.current()
    # Time passes past the TTL, and now every source is down.
    rates._cache.fetched_at -= rates.TTL_S + 1
    _stub_fetch(monkeypatch, None)
    data = await rates.current()
    assert data["source"] == "stale"
    assert data["rates"] == good["rates"] and data["asOf"] == "2026-09-10"


async def test_the_page_block_is_valid_json_the_script_can_read() -> None:
    data = rates._snapshot({"AED": 3.6725, "INR": 95.957344}, "2026-09-18", "x")
    html = rates.render("<body><!--PRICING_RATES--></body>", data)
    assert "<!--PRICING_RATES-->" not in html
    block = re.search(r'<script id="fx-rates" type="application/json">(.*?)</script>', html)
    assert block
    parsed = json.loads(block.group(1))
    assert parsed["base"] == "USD" and set(parsed["rates"]) == {"USD", "AED", "INR"}
    assert parsed["rates"]["INR"] == 95.9573  # four decimals is plenty


def test_the_parsers_read_each_source_shape() -> None:
    er = {"result": "success", "time_last_update_unix": 1789689751, "rates": {"AED": 3.6725, "INR": 95.957344}}
    got, as_of = rates._parse_er_api(er)
    assert got == {"AED": 3.6725, "INR": 95.957344} and as_of == "2026-09-18"
    fr = {"base": "USD", "date": "2026-09-17", "rates": {"AED": 3.67, "INR": 95.94}}
    got, as_of = rates._parse_frankfurter(fr)
    assert got == {"AED": 3.67, "INR": 95.94} and as_of == "2026-09-17"
    with pytest.raises(ValueError):
        rates._parse_er_api({"result": "error"})


async def test_the_landing_page_carries_the_rates_and_the_picker(monkeypatch) -> None:
    from pathlib import Path

    import app.web.router as web

    _stub_fetch(monkeypatch, None)
    page = Path(web._INDEX).read_text(encoding="utf-8")
    page = rates.render(page, await rates.current())
    assert 'id="fx-rates"' in page
    assert page.count('class="fx-pick"') == 2, "one picker for the plans, one for the glasses"
    # The four spec cards are the only glasses prices on the page: the
    # pricing section's hard-coded strip ("Add smart glasses", AED 300…450)
    # disagreed with the cards once a price was edited in the admin panel,
    # and was removed (Faraz, 2026-09-26).
    assert page.count('class="fx" data-aed=') == 4, "the four spec cards"
    assert "Add smart glasses" not in page
    assert "function fxApply" in page and "fxDetect" in page
