"""The Google API key must never appear in a URL.

Found while watching a D2 scan go past in the backend log:

    HTTP Request: POST https://vision.googleapis.com/v1/images:annotate?key=AIza…

httpx logs the full request line at INFO — the level this service runs at — so
every Vision and Gemini call printed the live key. Logs get pasted into chats and
shipped to aggregators, and this repo has already had runtime logs committed to
git once with live credentials in them (a3bb9cc). Google takes the key as a
header, so the URL never has to carry it.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.vision import _api_key_header, run_detection

_KEY = "AIzaSyFAKE-not-a-real-key-000000000000000"

#: A real (if boring) 32x32 JPEG. The detector decodes the image before it calls
#: out, so a fake byte string never reaches the network — the test would then
#: pass while proving nothing, which is how the first draft of it failed.
_TINY_JPEG = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAgACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCxRRRXYcgUUUUAFFFFABRRRQB//9k="

_VISION_OK = {
    "responses": [
        {
            "landmarkAnnotations": [
                {"description": "Test Landmark", "score": 0.9, "locations": []}
            ]
        }
    ]
}


class _Settings:
    """Just the fields run_detection reads."""

    vision_api_key = _KEY
    gemini_api_key = _KEY


@pytest.fixture
def seen(monkeypatch):
    """Capture every request the vision service makes, without any network."""
    calls: list[httpx.Request] = []

    async def fake_send(self, request, **kwargs):  # noqa: ANN001
        calls.append(request)
        return httpx.Response(200, json=_VISION_OK, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    return calls


def test_key_goes_in_a_header_not_the_url() -> None:
    assert _api_key_header(_KEY) == {"x-goog-api-key": _KEY}


async def test_no_request_ever_carries_the_key_in_its_url(seen) -> None:
    await run_detection(
        "landmark",
        settings=_Settings(),
        image_data="data:image/jpeg;base64," + _TINY_JPEG,
    )

    assert seen, "the detector made no request at all — test proves nothing"

    for req in seen:
        url = str(req.url)
        assert _KEY not in url, f"key leaked into the URL: {url}"
        assert "key=" not in url, f"a ?key= param is back: {url}"

    google = [r for r in seen if "googleapis.com" in str(r.url)]
    assert len(google) >= 2, "expected both the Vision and Gemini calls"
    for req in google:
        # It still has to travel — just in the header.
        assert req.headers.get("x-goog-api-key") == _KEY, str(req.url)

    # And it must not travel anywhere else. The detector also fetches Wikipedia;
    # our Google key has no business being sent to a third party.
    for req in (r for r in seen if "googleapis.com" not in str(r.url)):
        assert _KEY not in str(req.headers), f"key sent to {req.url.host}"
