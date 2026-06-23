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
