"""The WhatsApp buttons and the footer's social icons.

The page has promised "WhatsApp support" in its copy since it was written and
carried no number anywhere. These check the two things that promise depends on:
that a number set in the environment reaches every button in a form wa.me
accepts, and that a number which cannot work produces no button at all rather
than one that opens a chat with nobody.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import get_settings
from app.web import contact

pytestmark = pytest.mark.asyncio

_SLOTS = (
    "<!--CONTACT_CSS--><!--SOCIAL_LINKS--><!--WHATSAPP_CTA-->"
    "<!--WHATSAPP_FOOTER--><!--WHATSAPP_CARD:l801--><!--WHATSAPP_CARD:l802-->"
    "<!--WHATSAPP_CARD:gs4--><!--WHATSAPP_CARD:gs5-->"
    "<!--WHATSAPP_FAB--><!--CONTACT_JS-->"
)


# Every contact field starts unset, whatever the developer's own .env says:
# these tests describe the page with and without a number, not this machine.
_CONTACT_FIELDS = (
    "whatsapp_number",
    "whatsapp_hours",
    "social_instagram",
    "social_linkedin",
    "social_youtube",
    "social_facebook",
    "social_x",
)


def _settings(**overrides):
    clean = dict.fromkeys(_CONTACT_FIELDS, None)
    clean.update(overrides)
    return get_settings().model_copy(update=clean)


def _render(**overrides) -> str:
    return contact.render(_SLOTS, _settings(**overrides))


# ── the number ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "written",
    ["+971 50 123 4567", "971501234567", "+971-50-123-4567", "(971) 50 123 4567"],
)
async def test_a_number_is_accepted_however_it_is_written(written) -> None:
    """Nobody should have to know that wa.me wants bare digits."""
    assert contact.number(_settings(whatsapp_number=written)) == "971501234567"


@pytest.mark.parametrize(
    "written",
    ["", None, "12345", "  ", "not a number", "9715012345678901234"],
)
async def test_a_number_that_cannot_work_produces_no_button(written) -> None:
    """A local number with the country code forgotten would build a wa.me link
    that opens a chat with nobody — worse than no button at all."""
    settings = _settings(whatsapp_number=written)
    assert contact.number(settings) is None
    assert 'class="wa-fab"' not in contact.render(_SLOTS, settings)


async def test_nothing_is_rendered_when_nothing_is_configured() -> None:
    html = _render()
    assert html.strip() == ""


# ── the buttons ────────────────────────────────────────────────────────────


async def test_every_button_appears_once_the_number_is_set() -> None:
    html = _render(whatsapp_number="971501234567")
    # Count the markup, not the stylesheet, which names every class too.
    assert html.count('class="wa-fab"') == 1
    assert html.count('class="wa-card"') == 4  # one per glasses model
    assert 'class="wa-cta"' in html and 'class="wa-inline"' in html


async def test_each_button_says_where_it_came_from() -> None:
    """The whole value of a pre-filled message: an enquiry arrives already
    saying which card it was sent from, which is free here and impossible to
    reconstruct later."""
    html = _render(whatsapp_number="971501234567")
    texts = [
        parse_qs(urlparse(u).query)["text"][0]
        for u in re.findall(r'href="(https://wa\.me/[^"]+)"', html)
    ]
    assert len(texts) == 7
    assert len(set(texts)) == 7, "two buttons send the same message"
    for model in ("L801", "L802", "GS4", "GS5"):
        assert any(model in t for t in texts), model


async def test_every_whatsapp_link_is_a_wa_me_link_for_that_number() -> None:
    html = _render(whatsapp_number="+971 50 123 4567")
    urls = re.findall(r'href="(https://wa\.me/[^"]+)"', html)
    assert urls and all(u.startswith("https://wa.me/971501234567?text=") for u in urls)


async def test_the_reply_time_is_shown_when_set() -> None:
    """A button with no stated hours sets no expectation, and a slow reply
    then reads as being ignored."""
    assert "9am-6pm" in _render(
        whatsapp_number="971501234567", whatsapp_hours="Sun-Thu, 9am-6pm GST"
    )
    assert 'class="wa-hours"' not in _render(whatsapp_number="971501234567")


# ── the social icons ───────────────────────────────────────────────────────


async def test_only_the_networks_with_a_url_get_an_icon() -> None:
    html = _render(social_instagram="https://instagram.com/farryon")
    assert html.count('class="s-link"') == 1
    assert "instagram.com/farryon" in html
    assert "linkedin" not in html.lower()


async def test_a_network_marked_soon_shows_a_greyed_icon_and_no_link() -> None:
    """LinkedIn and YouTube are announced before their pages exist: the icon
    is there so the footer does not look like a one-network brand, but it
    links nowhere and says so."""
    html = _render(
        social_instagram="https://instagram.com/farryon",
        social_linkedin="soon",
        social_youtube="Coming soon",
    )
    assert html.count('class="s-link"') == 1  # the one real link
    assert html.count('class="s-link s-soon"') == 2
    assert 'aria-label="LinkedIn: coming soon"' in html
    assert 'aria-label="YouTube: coming soon"' in html
    # nothing links to a placeholder
    assert 'href="soon"' not in html and "href=\"Coming" not in html


@pytest.mark.parametrize(
    "hostile",
    ["javascript:alert(1)", "data:text/html,<script>x</script>", "/relative", "ftp://x"],
)
async def test_a_social_url_that_is_not_http_is_never_rendered(hostile) -> None:
    """These come from the environment and land inside an href."""
    assert contact.social_html(_settings(social_instagram=hostile)) == ""


# ── how the links behave ───────────────────────────────────────────────────


async def test_every_outbound_link_is_safe_to_open() -> None:
    html = _render(
        whatsapp_number="971501234567",
        social_instagram="https://instagram.com/farryon",
    )
    for tag in re.findall(r"<a [^>]*>", html):
        if "http" not in tag:
            continue
        assert 'target="_blank"' in tag, tag
        # Without noopener the opened tab can navigate this one.
        assert 'rel="noopener noreferrer"' in tag, tag


async def test_the_icon_only_buttons_say_what_they_are() -> None:
    """A screen reader announcing "link" and nothing else is a dead end."""
    html = _render(
        whatsapp_number="971501234567",
        social_instagram="https://instagram.com/farryon",
    )
    for tag in re.findall(r'<a class="(?:wa-fab|s-link)"[^>]*>', html):
        assert "aria-label=" in tag, tag


async def test_stylesheets_ship_only_when_something_needs_them() -> None:
    """Socials can be set without a number, and the other way round."""
    only_social = _render(social_instagram="https://instagram.com/farryon")
    assert ".footer-social .s-link" in only_social
    assert ".wa-fab{" not in only_social

    only_wa = _render(whatsapp_number="971501234567")
    assert ".wa-fab{" in only_wa
    assert ".footer-social .s-link" not in only_wa
