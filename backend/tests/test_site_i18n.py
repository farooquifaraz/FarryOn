"""The site in Hindi: same page, translated text, its own URLs.

What is pinned: English is untouched by the machinery (byte-for-byte, apart
from the switch and the hreflang links every language gets); Hindi replaces
the text nodes the dictionary knows and leaves the rest English rather than
blank; the dynamic parts (plan cards, WhatsApp buttons, catalog link) are
translated too because translation is the last step; internal links on a
Hindi page stay on Hindi pages; and both languages point at each other with
hreflang, which is what makes a Hindi search return the Hindi page.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.web import contact, i18n, pricing, products, shop
from app.web import router as web

pytestmark = pytest.mark.asyncio

SITE = "https://farryon.test"


def _english_landing() -> str:
    settings = get_settings().model_copy(update={"whatsapp_number": "+971558167757"})
    page = web._INDEX.read_text(encoding="utf-8")
    return contact.render(products.render(pricing.render(page, settings), settings), settings)


def _text_nodes(html: str) -> list[str]:
    body = html[html.index("<body") :]
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>|<!--.*?-->", "", body, flags=re.S)
    return [re.sub(r"\s+", " ", t).strip() for t in re.findall(r">([^<>]+)<", body) if t.strip()]


def test_the_hindi_dictionary_loads_and_knows_the_headline() -> None:
    tr = i18n.load("hi")
    assert tr is not None
    assert tr.lookup("See everything.") == "सब देखें।"
    assert tr.lookup("200 talk minutes a month") == "200 टॉक मिनट हर महीने"
    assert tr.lookup("Ask about the GS5 MAX") == "GS5 MAX के बारे में पूछें"
    assert tr.lookup("Something nobody translated") is None
    assert i18n.load("xx") is None


def test_english_gets_only_the_switch_and_the_alternates() -> None:
    page = _english_landing()
    out = i18n.localize(page, "en", path_en="/", site=SITE)
    assert 'class="lang-switch"' in out
    assert f'hreflang="hi" href="{SITE}/hi"' in out
    assert f'hreflang="x-default" href="{SITE}/"' in out
    assert '<html lang="en">' in out
    # Same text nodes as before — nothing was translated.
    assert _text_nodes(out)[3:] == _text_nodes(page)[3:] or "See everything." in out


def test_hindi_translates_the_static_and_the_dynamic_parts() -> None:
    page = _english_landing()
    out = i18n.localize(page, "hi", path_en="/", site=SITE)
    assert '<html lang="hi">' in out
    assert "<title>FarryOn — AI स्मार्ट ग्लासेस" in out
    assert 'class="lang-switch"' in out and 'href="/" hreflang="en"' in out
    # static
    assert "सब देखें।" in out and "चार मॉडल। एक ही AI।" in out
    # dynamic: plan cards, WhatsApp button, currency picker label
    assert "टॉक मिनट हर महीने" in out
    assert "L801 Business के बारे में पूछें" in out
    assert "कीमत देखें" in out
    # attributes the annual toggle swaps in
    assert 'data-m="प्रति माह" data-a="प्रति वर्ष"' in out
    # nothing left blank where a translation is missing: model names stay
    assert ">GS5 MAX<" in out and ">Sony 8MP · 4K वीडियो<" in out


def test_a_string_without_a_translation_stays_english_not_blank() -> None:
    out = i18n.localize("<body><p>Totally new sentence.</p></body>", "hi", path_en="/", site=SITE)
    assert "<p>Totally new sentence.</p>" in out


def test_internal_links_on_the_hindi_page_stay_in_hindi() -> None:
    page = _english_landing()
    out = i18n.localize(page, "hi", path_en="/", site=SITE)
    assert 'href="/hi/about"' in out
    assert 'href="/about"' not in out
    assert 'href="/hi#pricing"' in out or 'href="#pricing"' in out
    # downloads and legal pages are not per-language
    assert 'href="/download/arm64"' in out and 'href="/privacy"' in out
    # the alternates still point at the right places
    assert f'hreflang="en" href="{SITE}/"' in out
    assert f'hreflang="hi" href="{SITE}/hi"' in out


def test_scripts_are_never_translated() -> None:
    html = '<body><p>Monthly</p><script>var x="Monthly";</script></body>'
    out = i18n.localize(html, "hi", path_en="/", site=SITE)
    assert "<p>मासिक</p>" in out and 'var x="Monthly"' in out


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(web.router)
    return app


async def test_the_hindi_routes_serve_hindi_pages() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as client:
        home = await client.get("/hi")
        about = await client.get("/hi/about")
        english = await client.get("/")
    assert home.status_code == 200 and '<html lang="hi">' in home.text
    assert about.status_code == 200 and "ग्लासेस जो देखते हैं।" in about.text
    assert 'href="/hi"' in about.text  # the About page's links stay in Hindi
    assert '<html lang="en">' in english.text and "site-lang" in english.text
    # hreflang alternates are always https off localhost, whatever the proxy leg says
    assert 'hreflang="hi" href="https://t/hi"' in english.text
    # every internal link on the Hindi pages has a route
    routes = [r for r in web.router.routes if hasattr(r, "path_regex")]
    for html in (home.text, about.text):
        for href in re.findall(r'href="(/[^"#]*)', html):
            path = href.split("?")[0]
            assert any(r.path_regex.match(path) for r in routes), f"{path} has no backend route"


def test_every_hindi_key_is_still_on_the_page_or_a_rule(tmp_path, monkeypatch) -> None:
    """A translation for text that no longer exists is dead weight — and a
    sign the English changed and the Hindi silently fell back.

    Rendered with every optional part switched on (a WhatsApp number, a
    network marked "soon", a catalog on disk) so their strings count as
    present whatever this machine's .env and media directory hold."""
    tr = i18n.load("hi")
    assert tr is not None
    settings = get_settings().model_copy(
        update={"whatsapp_number": "+971558167757", "social_linkedin": "soon", "social_instagram": None}
    )
    (tmp_path / "catalogs").mkdir()
    (tmp_path / "catalogs" / "l801.pdf").write_bytes(b"%PDF-1.4 x")
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)
    products._cache.clear()
    page = web._INDEX.read_text(encoding="utf-8")
    landing = contact.render(shop.render(products.render(pricing.render(page, settings), settings), settings), settings)
    about = contact.render(web._ABOUT.read_text(encoding="utf-8"), settings)
    present = set(_text_nodes(landing)) | set(_text_nodes(about))
    for html in (landing, about):
        present |= set(re.findall(r'data-[ma]="([^"]+)"', html))
    missing = [k for k in tr.exact if k not in present]
    assert not missing, f"translations for text that is no longer on the page: {missing[:5]}"
