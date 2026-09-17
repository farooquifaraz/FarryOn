"""The spec cards show the photographs that exist, and nothing when none do.

Two properties matter enough to pin down. The first is that adding this feature
cost the shipped site nothing: with no photographs on disk the page must render
byte-for-byte as it did before, because that is the state the site is in until
a shoot happens. The second is that ``/media`` serves only what it is meant to
— it is the one route that turns a URL into a filesystem path.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import get_settings
from app.web import pricing, products
from app.web import router as web

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def settings():
    return get_settings()


@pytest.fixture()
def page() -> str:
    return web._INDEX.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_scan_cache():
    """Each test gets a cold scan — the cache is keyed on mtime, and two tmp
    directories created in the same nanosecond would otherwise collide."""
    products._cache.clear()
    yield
    products._cache.clear()


def _media(root, slug: str = "l801", shots=("front", "angle"), video=False):
    """Write a small but real media set for one model."""
    directory = root / slug
    directory.mkdir(parents=True, exist_ok=True)
    for shot in shots:
        for width in products._WIDTHS:
            Image.new("RGB", (width, width * 3 // 4), (20, 30, 40)).save(
                directory / f"{shot}-{width}.webp", "WEBP"
            )
        Image.new("RGB", (1600, 1200), (20, 30, 40)).save(
            directory / f"{shot}.jpg", "JPEG"
        )
    if video:
        (directory / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        Image.new("RGB", (16, 12)).save(directory / "video-poster.jpg", "JPEG")
    return directory


# ── the guarantee ──────────────────────────────────────────────────────────


async def test_no_media_leaves_the_page_byte_for_byte_unchanged(
    settings, page, tmp_path, monkeypatch
) -> None:
    """The whole point: a site with no photographs is the site as it shipped.

    The comparison is against the page with its media slots deleted outright —
    which is what index.html was before this feature. If the slots ever cost a
    byte of their own (a stray newline, an indent) this is what catches it.
    """
    import re

    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path / "nothing")
    as_it_shipped = re.sub(r"<!--PRODUCT_MEDIA[^>]*?-->", "", page)
    rendered = products.render(pricing.render(page, settings), settings)
    assert rendered == pricing.render(as_it_shipped, settings)


async def test_the_placeholders_leave_no_trace(settings, page, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path / "nothing")
    rendered = products.render(pricing.render(page, settings), settings)
    assert "PRODUCT_MEDIA" not in rendered


async def test_every_placeholder_the_page_carries_is_one_we_fill(page) -> None:
    """A slot nobody fills would ship a raw comment to production."""
    import re

    slots = set(re.findall(r"<!--(PRODUCT_MEDIA[^>]*?)-->", page))
    expected = {"PRODUCT_MEDIA_CSS", "PRODUCT_MEDIA_JS"} | {
        f"PRODUCT_MEDIA:{slug}" for slug in products.MODELS
    }
    assert slots == expected


# ── discovery ──────────────────────────────────────────────────────────────


async def test_a_shoot_on_disk_becomes_a_gallery(settings, page, tmp_path, monkeypatch) -> None:
    _media(tmp_path, "l801", shots=("front", "angle", "camera"), video=True)
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)

    gallery = products.galleries(settings)["l801"]
    assert [shot for shot, _, _ in gallery.stills] == ["front", "angle", "camera"]
    assert gallery.video and gallery.poster == "video-poster.jpg"
    assert gallery.count == 4  # three stills + the video

    rendered = products.render(pricing.render(page, settings), settings)
    assert 'data-pm="l801"' in rendered
    # The model with no media stays exactly as it was.
    assert 'data-pm="l802"' not in rendered
    # Stylesheet and script ride along only because something needs them.
    assert "PRODUCT MEDIA" in rendered and 'id="pm-lb"' in rendered


async def test_shots_are_offered_smallest_first_with_a_universal_fallback(
    settings, tmp_path, monkeypatch
) -> None:
    _media(tmp_path, "l801", shots=("front",))
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)
    html = products.gallery_html(products.galleries(settings)["l801"])

    assert 'type="image/webp"' in html
    assert "front-400.webp 400w" in html
    assert html.index("front-400.webp") < html.index("front-1600.webp")
    # The <img> a browser without srcset support falls back to must be a
    # format every browser can decode.
    assert 'src="/media/l801/front.jpg"' in html


async def test_a_lone_jpeg_still_renders(settings, tmp_path, monkeypatch) -> None:
    """Before anyone runs the optimizer there is one file per shot."""
    directory = tmp_path / "l801"
    directory.mkdir(parents=True)
    Image.new("RGB", (900, 675)).save(directory / "front.jpg", "JPEG")
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)

    html = products.gallery_html(products.galleries(settings)["l801"])
    assert 'src="/media/l801/front.jpg"' in html
    assert "<source" not in html  # nothing to offer, so nothing is claimed
    # One slide needs no arrows and no thumbnail strip.
    assert "pm-nav" not in html and "pm-thumbs" not in html


async def test_every_photo_carries_alt_text(settings, tmp_path, monkeypatch) -> None:
    """A product photo with no alt text is invisible to a screen reader and to
    Google Images — the two audiences least able to ask for a description."""
    import re

    _media(tmp_path, "l801", shots=("front", "angle"))
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)
    html = products.gallery_html(products.galleries(settings)["l801"])

    stage = html.split('class="pm-thumbs"')[0]
    alts = re.findall(r'<img[^>]*\salt="([^"]*)"', stage)
    assert alts and all(a.strip() for a in alts)
    assert all("L801 Business" in a for a in alts)


async def test_new_files_are_picked_up_without_a_restart(
    settings, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)
    (tmp_path / "l801").mkdir()
    assert not products.galleries(settings)["l801"]

    _media(tmp_path, "l801", shots=("front",))
    assert products.galleries(settings)["l801"]


# ── the /media route ───────────────────────────────────────────────────────


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(web.router)
    return app


async def _get(path: str):
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://t"
    ) as client:
        return await client.get(path)


async def test_a_real_photo_is_served_and_cached(tmp_path, monkeypatch) -> None:
    _media(tmp_path, "l801", shots=("front",))
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)

    response = await _get("/media/l801/front-800.webp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert "max-age=86400" in response.headers["cache-control"]
    # `immutable` would make a re-shoot saved over an existing filename
    # invisible to everyone who already loaded the page.
    assert "immutable" not in response.headers["cache-control"]
    assert response.headers.get("etag")
    assert response.content[:4] == b"RIFF"


@pytest.mark.parametrize(
    "path",
    [
        "/media/l801/../../config.py",       # traversal
        "/media/l801/..%2f..%2fconfig.py",   # encoded traversal
        "/media/../l801/front.jpg",          # traversal in the model segment
        "/media/l999/front.jpg",             # a model we do not sell
        "/media/l801/router.py",             # an extension the gallery never uses
        "/media/l801/.env",                  # a dotfile
        "/media/l801/",                      # no filename at all
    ],
)
async def test_the_media_route_refuses_anything_it_did_not_offer(
    path, tmp_path, monkeypatch
) -> None:
    _media(tmp_path, "l801", shots=("front",))
    (tmp_path / "l801" / "router.py").write_text("secret")
    (tmp_path / "l801" / ".env").write_text("SECRET=1")
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)

    response = await _get(path)
    assert response.status_code in (404, 307), path
    assert b"secret" not in response.content.lower()


async def test_a_missing_file_is_a_plain_404(tmp_path, monkeypatch) -> None:
    (tmp_path / "l801").mkdir()
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)
    assert (await _get("/media/l801/front.jpg")).status_code == 404


async def test_the_page_permits_the_video_it_embeds(tmp_path, monkeypatch) -> None:
    """A Content-Security-Policy without media-src blocks every product video."""
    monkeypatch.setattr(products, "media_root", lambda _s: tmp_path)
    response = await _get("/")
    assert response.status_code == 200
    assert "media-src 'self'" in response.headers["content-security-policy"]


# ── the manifest and the optimizer agree ───────────────────────────────────


async def test_the_route_serves_exactly_what_the_gallery_asks_for() -> None:
    """ALLOWED_SUFFIXES is the contract between products.py and the route; a
    format added to one and not the other renders a broken image."""
    referenced = {ext for ext, _ in products._MODERN_STILLS}
    referenced |= set(products._FALLBACK_STILLS)
    referenced |= {ext for ext, _ in products._VIDEOS}
    assert referenced <= products.ALLOWED_SUFFIXES
    assert products.ALLOWED_SUFFIXES <= set(products.MEDIA_TYPES)
