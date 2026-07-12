"""Tests for the vision detection service (no network — httpx is mocked).

A :class:`httpx.MockTransport` routes Vision / Wikipedia / Gemini calls to
canned responses, so :func:`app.services.vision.run_detection` is exercised
end-to-end (including Pillow image normalization) fully offline.
"""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.services import vision

pytestmark = pytest.mark.asyncio


def _tiny_jpeg_b64() -> str:
    """A real 2×2 JPEG as base64 so normalize_image_b64 can decode it."""
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _handler(*, landmark=True, product=True):
    """Build a MockTransport handler for Vision/Wikipedia/Gemini."""

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "vision.googleapis.com" in url:
            body = json.loads(request.content)
            feature = body["requests"][0]["features"][0]["type"]
            if feature == "LANDMARK_DETECTION":
                anns = (
                    [
                        {
                            "description": "Eiffel Tower",
                            "score": 0.91,
                            "locations": [
                                {"latLng": {"latitude": 48.8584, "longitude": 2.2945}}
                            ],
                        }
                    ]
                    if landmark
                    else []
                )
                return httpx.Response(
                    200, json={"responses": [{"landmarkAnnotations": anns}]}
                )
            # WEB_DETECTION
            web = (
                {
                    "bestGuessLabels": [{"label": "Apple Watch Series 9"}],
                    "webEntities": [{"description": "Smartwatch"}, {"description": "Apple"}],
                    "pagesWithMatchingImages": [
                        {"pageTitle": "Apple Watch", "url": "https://apple.com/watch"}
                    ],
                    "visuallySimilarImages": [{"url": "https://img/1.jpg"}],
                }
                if product
                else {}
            )
            return httpx.Response(200, json={"responses": [{"webDetection": web}]})
        if "en.wikipedia.org" in url:
            return httpx.Response(
                200,
                json={
                    "extract": "A wrought-iron tower in Paris.",
                    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Eiffel_Tower"}},
                },
            )
        if "generativelanguage.googleapis.com" in url:
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "Ye ek smartwatch hai."}]}}]},
            )
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    return handle


@pytest.fixture
def mock_httpx(monkeypatch):
    """Patch vision.httpx.AsyncClient to use a MockTransport handler."""

    def install(*, landmark=True, product=True):
        transport = httpx.MockTransport(_handler(landmark=landmark, product=product))
        orig = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return orig(*args, **kwargs)

        monkeypatch.setattr(vision.httpx, "AsyncClient", factory)

    return install


def _settings() -> Settings:
    return Settings(vision_api_key="vk", gemini_api_key="gk")


async def test_landmark_detection(mock_httpx) -> None:
    mock_httpx(landmark=True)
    env = await vision.run_detection(
        "landmark", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    assert env["ok"] is True
    assert env["mode"] == "landmark"
    lm = env["result"]["landmarks"][0]
    assert lm["name"] == "Eiffel Tower"
    assert lm["maps_url"].endswith("48.8584,2.2945")
    assert "Paris" in lm["description"]
    assert lm["wikipedia_url"].endswith("Eiffel_Tower")


async def test_product_detection(mock_httpx) -> None:
    mock_httpx()
    env = await vision.run_detection(
        "product", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    assert env["ok"] is True
    assert env["mode"] == "product"
    res = env["result"]
    assert res["product_name"] == "Apple Watch Series 9"
    assert any(m["region"] == "Gulf" for m in res["marketplaces"])
    assert res["ai_explanation"] == "Ye ek smartwatch hai."


async def test_auto_falls_back_to_product(mock_httpx) -> None:
    """auto: no landmark found -> product detection runs."""
    mock_httpx(landmark=False, product=True)
    env = await vision.run_detection(
        "auto", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    assert env["ok"] is True
    assert env["mode"] == "product"
    assert env["result"]["product_name"] == "Apple Watch Series 9"


async def test_landmark_has_source_and_maps(mock_httpx) -> None:
    """Landmark results are attributed to Google Vision and always link to Maps."""
    mock_httpx(landmark=True)
    env = await vision.run_detection(
        "landmark", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    assert env["result"]["source"] == "Google Vision API"
    assert env["result"]["landmarks"][0]["maps_url"]


async def test_landmark_without_gps_still_has_maps_link(monkeypatch) -> None:
    """A recognised landmark with NO coordinates still gets a Maps search link."""

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "vision.googleapis.com" in url:
            # LANDMARK_DETECTION hit but with no locations array.
            return httpx.Response(
                200,
                json={
                    "responses": [
                        {"landmarkAnnotations": [{"description": "Some Old Fort", "score": 0.8}]}
                    ]
                },
            )
        return httpx.Response(404, json={})

    _install_handler(monkeypatch, handle)
    env = await vision.run_detection(
        "landmark", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    lm = env["result"]["landmarks"][0]
    assert lm["location"] is None
    assert lm["maps_url"] and "google.com/maps/search" in lm["maps_url"]
    assert "Some+Old+Fort" in lm["maps_url"]


async def test_product_source_attribution(mock_httpx) -> None:
    """Product results carry a `source` crediting who identified the item."""
    mock_httpx()  # Gemini returns non-JSON here → gem is None → Vision names it
    env = await vision.run_detection(
        "product", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    assert env["result"]["source"] == "Google Vision API"


async def test_web_mode_needs_no_key() -> None:
    env = await vision.run_detection(
        "web", settings=Settings(), image_url="https://example.com/x.jpg"
    )
    assert env["ok"] is True
    assert env["mode"] == "web"
    assert env["result"]["lens_url"].startswith("https://lens.google.com/uploadbyurl?url=")


async def test_missing_vision_key_is_friendly() -> None:
    # _env_file=None so a real key in the developer's .env can't leak in and
    # make this "no key" test pass a real request through.
    env = await vision.run_detection(
        "landmark",
        settings=Settings(_env_file=None, vision_api_key=None),
        image_data=_tiny_jpeg_b64(),
    )
    assert env["ok"] is False
    assert "VISION_API_KEY" in env["error"]


def _install_handler(monkeypatch, handle) -> None:
    """Patch vision.httpx.AsyncClient to route through a MockTransport."""
    transport = httpx.MockTransport(handle)
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        vision.httpx,
        "AsyncClient",
        lambda *a, **k: orig(*a, transport=transport, **k),
    )


async def test_malformed_vision_response_is_friendly(monkeypatch) -> None:
    """A 200 with an unexpected body must yield {ok:false}, not raise/500."""

    def handle(request: httpx.Request) -> httpx.Response:
        if "vision.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"responses": []})  # IndexError bait
        return httpx.Response(404, json={"error": {"message": "x"}})

    _install_handler(monkeypatch, handle)
    env = await vision.run_detection(
        "landmark", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    assert env["ok"] is False
    assert env["error"]  # a friendly message, no exception escaped


# -- Vision API call-budget tests (cost control) -----------------------------
# The bill is driven by how many Google Vision `images:annotate` calls each
# request makes. These lock the contract: Vision is only ever hit on a real
# identify (landmark/product), and AT MOST ONCE — Gemini classifies first so a
# product never wastes a landmark call and vice-versa.

def _counting_handler(*, gem_kind: str = "product", gem_name: str = "Test Item",
                       landmark_found: bool = False):
    """MockTransport handler that COUNTS Vision + Gemini calls.

    Gemini returns valid JSON (so ``gem`` is populated and its ``kind`` drives
    routing). Returns ``(handler, counts)``.
    """
    counts = {"vision": 0, "gemini": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "vision.googleapis.com" in url:
            counts["vision"] += 1
            feature = json.loads(request.content)["requests"][0]["features"][0]["type"]
            if feature == "LANDMARK_DETECTION":
                anns = (
                    [{"description": "Eiffel Tower", "score": 0.9,
                      "locations": [{"latLng": {"latitude": 48.8, "longitude": 2.3}}]}]
                    if landmark_found else []
                )
                return httpx.Response(200, json={"responses": [{"landmarkAnnotations": anns}]})
            return httpx.Response(
                200,
                json={"responses": [{"webDetection": {
                    "bestGuessLabels": [{"label": "Widget"}], "webEntities": []}}]},
            )
        if "generativelanguage.googleapis.com" in url:
            counts["gemini"] += 1
            gem = json.dumps({"kind": gem_kind, "name": gem_name,
                              "description": "d", "where": None, "details": []})
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": gem}]}}]})
        if "en.wikipedia.org" in url:
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    return handle, counts


async def test_auto_product_makes_exactly_one_vision_call(monkeypatch) -> None:
    """auto + Gemini says 'product' → ONE Vision call (web), NOT a wasted
    landmark call. This is the core bill fix."""
    handle, counts = _counting_handler(gem_kind="product")
    _install_handler(monkeypatch, handle)
    env = await vision.run_detection("auto", settings=_settings(), image_data=_tiny_jpeg_b64())
    assert env["ok"] and env["mode"] == "product"
    assert counts["vision"] == 1


async def test_auto_place_makes_exactly_one_vision_call(monkeypatch) -> None:
    """auto + Gemini says 'place' → ONE Vision call (landmark), no web call."""
    handle, counts = _counting_handler(gem_kind="landmark", gem_name="Some Plaza")
    _install_handler(monkeypatch, handle)
    env = await vision.run_detection("auto", settings=_settings(), image_data=_tiny_jpeg_b64())
    assert env["ok"] and env["mode"] == "landmark"
    assert counts["vision"] == 1


async def test_landmark_mode_one_vision_call(monkeypatch) -> None:
    handle, counts = _counting_handler(gem_kind="landmark", landmark_found=True)
    _install_handler(monkeypatch, handle)
    env = await vision.run_detection("landmark", settings=_settings(), image_data=_tiny_jpeg_b64())
    assert env["ok"] and env["mode"] == "landmark"
    assert counts["vision"] == 1


async def test_product_mode_one_vision_call(monkeypatch) -> None:
    handle, counts = _counting_handler(gem_kind="product")
    _install_handler(monkeypatch, handle)
    env = await vision.run_detection("product", settings=_settings(), image_data=_tiny_jpeg_b64())
    assert env["ok"] and env["mode"] == "product"
    assert counts["vision"] == 1


async def test_question_mode_makes_no_vision_call(monkeypatch) -> None:
    """A read/answer question ('what time is it?') is Gemini-only — 0 Vision."""
    handle, counts = _counting_handler()
    _install_handler(monkeypatch, handle)
    env = await vision.run_detection(
        "auto", settings=_settings(), image_data=_tiny_jpeg_b64(),
        question="what time does the clock show?",
    )
    assert env["ok"]
    assert counts["vision"] == 0


async def test_web_mode_makes_no_vision_call(monkeypatch) -> None:
    handle, counts = _counting_handler()
    _install_handler(monkeypatch, handle)
    env = await vision.run_detection(
        "web", settings=Settings(vision_api_key="vk"), image_url="https://x/y.jpg")
    assert env["ok"] and env["mode"] == "web"
    assert counts["vision"] == 0


async def test_vision_call_counter_increments(monkeypatch) -> None:
    """The /metrics Vision counter goes up by exactly 1 per billed call."""
    from prometheus_client import REGISTRY

    def ok_count() -> float:
        return REGISTRY.get_sample_value(
            "farryon_vision_api_calls_total",
            {"feature": "WEB_DETECTION", "outcome": "ok"},
        ) or 0.0

    handle, counts = _counting_handler(gem_kind="product")
    _install_handler(monkeypatch, handle)
    before = ok_count()
    await vision.run_detection("product", settings=_settings(), image_data=_tiny_jpeg_b64())
    assert counts["vision"] == 1
    assert ok_count() == before + 1  # one WEB_DETECTION unit counted


async def test_gemini_carries_when_vision_down(monkeypatch) -> None:
    """Vision 403 (quota/billing) must NOT sink a good Gemini identification.

    Locks the robustness contract: as long as Gemini can identify the subject,
    the user gets a product card even if Google Vision is completely down.
    """

    gem_json = json.dumps(
        {
            "kind": "product",
            "name": "Sony WH-1000XM5 Headphones",
            "description": "Noise-cancelling over-ear headphones.",
            "where": None,
            "details": ["Bluetooth 5.2", "30h battery"],
        }
    )

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "vision.googleapis.com" in url:
            # Vision totally unavailable (billing disabled / key revoked).
            return httpx.Response(403, json={"error": {"message": "SERVICE_DISABLED"}})
        if "generativelanguage.googleapis.com" in url:
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": gem_json}]}}]},
            )
        return httpx.Response(404, json={})

    _install_handler(monkeypatch, handle)
    env = await vision.run_detection(
        "auto", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    assert env["ok"] is True
    assert env["mode"] == "product"
    assert env["result"]["product_name"] == "Sony WH-1000XM5 Headphones"
    # Marketplace links are built from the Gemini name, so shopping still works.
    assert env["result"]["marketplaces"]


async def test_gemini_falls_back_on_throttle(monkeypatch) -> None:
    """A 429 from the primary Gemini model must try the fallback model."""

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "vision.googleapis.com" in url:
            return httpx.Response(
                200,
                json={
                    "responses": [
                        {
                            "webDetection": {
                                "bestGuessLabels": [{"label": "Widget"}],
                                "webEntities": [],
                            }
                        }
                    ]
                },
            )
        if "generativelanguage.googleapis.com" in url:
            if "gemini-2.0-flash" in url:
                return httpx.Response(429, json={})  # throttled
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "fallback ok"}]}}]},
            )
        return httpx.Response(404, json={})

    _install_handler(monkeypatch, handle)
    env = await vision.run_detection(
        "product", settings=_settings(), image_data=_tiny_jpeg_b64()
    )
    assert env["ok"] is True
    assert env["result"]["ai_explanation"] == "fallback ok"
