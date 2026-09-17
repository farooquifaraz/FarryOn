"""No link on the marketing page may point at nothing.

The page shipped with eleven links to ``#`` — four social icons, Changelog,
About, Blog, Careers, Press, SDK Docs and WhatsApp — plus a "Sign in" pointing
at ``/login``, which is the *admin* SPA's login and rejects anyone who is not
staff. On a page asking AED 350 from an unfamiliar brand, a link that does
nothing when clicked is not cosmetic: it is the visitor's evidence that nobody
is home.

These tests are what stops them coming back, and they check the three ways a
link can be dead: pointing at ``#``, pointing at a path the backend does not
serve, and pointing at a path the edge proxy never forwards to the backend.
That last one is not hypothetical — the product galleries' /media route was
written, tested and working while Caddy quietly sent every image request to
the admin SPA instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.web import contact, pricing, products
from app.web import router as web

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[2]
_CADDYFILE = _REPO / "admin" / "Caddyfile"

# Configured the way a live deployment is, so the links that only exist when
# a number is set are covered too.
_LIVE = {
    "whatsapp_number": "+971 50 123 4567",
    "whatsapp_hours": "Sun-Thu, 9am-6pm GST",
    "social_instagram": "https://instagram.com/farryon",
    "social_linkedin": "https://linkedin.com/company/farryon",
}


# Every contact field starts unset, whatever the developer's own .env says:
# these tests describe the page with and without a number, not this machine.
def _clean_settings():
    return get_settings().model_copy(
        update=dict.fromkeys(
            (
                "whatsapp_number",
                "whatsapp_hours",
                "social_instagram",
                "social_linkedin",
                "social_youtube",
                "social_facebook",
                "social_x",
            ),
            None,
        )
    )


def _render(**overrides) -> str:
    """Both public pages, concatenated: a dead link on either is a dead link."""
    settings = _clean_settings().model_copy(update=overrides)
    landing = web._INDEX.read_text(encoding="utf-8")
    landing = contact.render(
        products.render(pricing.render(landing, settings), settings), settings
    )
    about = contact.render(web._ABOUT.read_text(encoding="utf-8"), settings)
    return landing + about


def _hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]*)"', html)


def _internal(html: str) -> set[str]:
    """Site-absolute links only — not anchors, not external, not the fonts CDN."""
    return {h for h in _hrefs(html) if h.startswith("/")}


@pytest.fixture()
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(web.router)
    return application


# ── nothing points at "#" ──────────────────────────────────────────────────


@pytest.mark.parametrize("configured", [False, True])
async def test_no_link_points_at_nothing(configured) -> None:
    html = _render(**(_LIVE if configured else {}))
    dead = [h for h in _hrefs(html) if h in ("#", "", "javascript:void(0)")]
    assert not dead, f"{len(dead)} link(s) still go nowhere"


async def test_the_unset_page_simply_has_fewer_links_not_broken_ones() -> None:
    """With no number and no profiles, those links are absent — not stubs."""
    html = _render()
    # The stylesheet still defines .s-link and .wa-fab; what must be absent is
    # any element using them.
    assert 'class="wa-fab"' not in html
    assert 'class="s-link"' not in html
    assert "WHATSAPP" not in html and "SOCIAL_LINKS" not in html


# ── every internal link is served by the backend ───────────────────────────


async def test_every_internal_link_has_a_backend_route(app) -> None:
    """The marketing page is backend-rendered; a path the backend does not
    serve falls through the edge proxy to the admin SPA, which is staff-only.
    That is how "Sign in" came to send customers to an admin login."""
    # The router's own routes, not app.routes: this FastAPI version keeps an
    # included router as a single opaque entry rather than flattening it.
    routes = [r for r in web.router.routes if hasattr(r, "path_regex")]
    for href in _internal(_render(**_LIVE)):
        path = href.split("?")[0].split("#")[0]
        assert any(r.path_regex.match(path) for r in routes), (
            f"{path} is linked from the landing page but no backend route "
            f"serves it — it would render the admin SPA"
        )


async def test_the_pages_those_links_reach_actually_render(app) -> None:
    """Spot-check the static pages rather than trusting the route table."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        for path in ("/", "/about", "/privacy", "/terms"):
            response = await client.get(path)
            assert response.status_code == 200, path
            # Every backend-rendered page carries the site's own CSP; the
            # app-wide default is `default-src 'none'`, which blanks a page.
            assert "img-src 'self'" in response.headers["content-security-policy"], path


# ── the edge proxy forwards them ───────────────────────────────────────────


def _caddy_backend_paths() -> list[str]:
    line = next(
        ln for ln in _CADDYFILE.read_text(encoding="utf-8").splitlines()
        if ln.strip().startswith("@backend path ")
    )
    return line.strip().removeprefix("@backend path ").split()


def _proxied(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("*"):
            if path.startswith(pattern[:-1]):
                return True
        elif path == pattern:
            return True
    return False


@pytest.mark.skipif(not _CADDYFILE.is_file(), reason="edge config not in this tree")
async def test_every_linked_path_is_forwarded_to_the_backend() -> None:
    """Caddy matches an explicit list and sends everything else to the SPA, so
    a new backend route is only half-deployed until it is named here."""
    patterns = _caddy_backend_paths()
    for href in _internal(_render(**_LIVE)):
        path = href.split("?")[0].split("#")[0]
        assert _proxied(path, patterns), (
            f"{path} is linked from the landing page but Caddy does not "
            f"forward it to the backend — the admin SPA would answer it"
        )


@pytest.mark.skipif(not _CADDYFILE.is_file(), reason="edge config not in this tree")
async def test_product_media_is_forwarded_to_the_backend() -> None:
    """The galleries' images are requested by <img>, so they never appear in an
    href and the test above cannot see them."""
    patterns = _caddy_backend_paths()
    for slug in products.MODELS:
        assert _proxied(f"/media/{slug}/front-800.webp", patterns), (
            "Caddy does not forward /media — every product photo would come "
            "back as the admin SPA's index.html"
        )


# ── the About page ─────────────────────────────────────────────────────────


async def test_the_about_page_is_reachable_from_both_pages() -> None:
    """A trust page nobody can find is not a trust page."""
    html = _render()
    landing = html.split("<!DOCTYPE html>")[1]
    assert 'href="/about"' in landing.split("<footer>")[0], "not in the nav"
    assert 'href="/about"' in landing.split("<footer>")[1], "not in the footer"


async def test_the_about_page_gets_its_own_whatsapp_button() -> None:
    settings = _clean_settings().model_copy(update=_LIVE)
    about = contact.render(web._ABOUT.read_text(encoding="utf-8"), settings)
    assert 'class="wa-cta"' in about and "9am-6pm" in about
    assert "About%20page" in about, "the About button should say where it came from"
    # And, unset, no stub is left behind.
    assert "WHATSAPP" not in contact.render(web._ABOUT.read_text(encoding="utf-8"), _clean_settings())


async def test_the_about_page_makes_no_claim_the_site_cannot_back() -> None:
    """Written without the facts only the founders have — so it must not
    pretend to have them. Anyone adding one of these should add the fact too."""
    about = web._ABOUT.read_text(encoding="utf-8").lower()
    for word in ("founded in", "patent", "award", "customers worldwide", "investors", "our team of"):
        assert word not in about, f"About page claims '{word}' with nothing behind it"
