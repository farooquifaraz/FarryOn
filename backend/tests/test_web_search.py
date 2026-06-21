"""Tests for the web_search tool — real-provider parsing + fallback, no network.

``httpx.AsyncClient`` is replaced with a fake whose routes map a URL to either a
canned JSON payload or an exception. This covers the provider response parsing
(tavily / serper / serpapi), the answer-box promotion, the primary→fallback
chain on quota errors, and the final mock fallback — previously all uncovered.
"""

from __future__ import annotations

import httpx
import pytest

from app.tools import web_search
from app.tools.base import ToolContext
from app.tools.web_search import WebSearchTool

pytestmark = pytest.mark.asyncio


class _FakeResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:  # routes raise before this for errors
        return None

    def json(self) -> dict:
        return self._data


class _FakeClient:
    """Async-context httpx stand-in; ``routes`` maps URL -> payload | Exception."""

    routes: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def _respond(self, url: str) -> _FakeResponse:
        val = type(self).routes[url]
        if isinstance(val, Exception):
            raise val
        return _FakeResponse(val)

    async def post(self, url: str, **kwargs) -> _FakeResponse:
        return self._respond(url)

    async def get(self, url: str, **kwargs) -> _FakeResponse:
        return self._respond(url)


@pytest.fixture
def fake_httpx(monkeypatch):
    """Install the fake client and return a setter for its routes."""

    def set_routes(routes: dict) -> None:
        _FakeClient.routes = routes

    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeClient)
    yield set_routes
    _FakeClient.routes = {}


def _ctx(**override) -> ToolContext:
    return ToolContext(session=None, web_search=override)


async def test_tavily_parses_results_and_promotes_answer(fake_httpx) -> None:
    """Tavily results parse, and a synthesized answer is promoted to the top."""
    fake_httpx(
        {
            "https://api.tavily.com/search": {
                "answer": "Paris is the capital.",
                "results": [
                    {"title": "T1", "url": "u1", "content": "c1"},
                    {"title": "T2", "url": "u2", "content": "c2"},
                ],
            }
        }
    )
    out = await WebSearchTool().run(
        _ctx(provider="tavily", apiKey="k"), query="capital of france"
    )
    assert out["provider"] == "tavily"
    assert out["results"][0] == {
        "title": "Answer",
        "url": "",
        "snippet": "Paris is the capital.",
    }
    assert out["results"][1]["title"] == "T1"


async def test_serper_maps_link_and_promotes_answer_box(fake_httpx) -> None:
    """Serper maps ``link``→url and lifts the answer box to the first result."""
    fake_httpx(
        {
            "https://google.serper.dev/search": {
                "answerBox": {"answer": "42"},
                "organic": [{"title": "S1", "link": "https://s1", "snippet": "s"}],
            }
        }
    )
    out = await WebSearchTool().run(
        _ctx(provider="serper", apiKey="k"), query="meaning of life"
    )
    assert out["provider"] == "serper"
    assert out["results"][0]["snippet"] == "42"
    assert out["results"][1]["url"] == "https://s1"


async def test_serpapi_parses_organic_results(fake_httpx) -> None:
    """SerpAPI maps ``organic_results`` into the common result shape."""
    fake_httpx(
        {
            "https://serpapi.com/search.json": {
                "organic_results": [
                    {"title": "G1", "link": "https://g1", "snippet": "g"},
                ]
            }
        }
    )
    out = await WebSearchTool().run(
        _ctx(provider="serpapi", apiKey="k"), query="x"
    )
    assert out["provider"] == "serpapi"
    assert out["results"] == [{"title": "G1", "url": "https://g1", "snippet": "g"}]


async def test_primary_quota_error_falls_back_to_secondary(fake_httpx) -> None:
    """When the primary errors (quota), the fallback provider is used."""
    fake_httpx(
        {
            "https://api.tavily.com/search": httpx.HTTPError("402 out of credits"),
            "https://google.serper.dev/search": {
                "organic": [{"title": "F", "link": "https://f", "snippet": "f"}]
            },
        }
    )
    out = await WebSearchTool().run(
        _ctx(
            provider="tavily",
            apiKey="k1",
            fallbackProvider="serper",
            fallbackApiKey="k2",
        ),
        query="q",
    )
    assert out["provider"] == "serper"  # moved on from the exhausted primary
    assert out["results"][0]["title"] == "F"


async def test_all_providers_fail_returns_mock_with_error(fake_httpx) -> None:
    """If every provider errors, deterministic mock results carry the last error."""
    fake_httpx(
        {"https://api.tavily.com/search": httpx.HTTPError("boom")}
    )
    out = await WebSearchTool().run(_ctx(provider="tavily", apiKey="k"), query="q")
    assert out["provider"] == "mock"
    assert "all providers failed" in out["error"]
    assert len(out["results"]) == 3  # _mock_results returns three


async def test_unknown_provider_falls_through_to_mock(fake_httpx) -> None:
    """An unrecognized provider name yields mock results rather than crashing."""
    out = await WebSearchTool().run(
        _ctx(provider="nope", apiKey="k"), query="hello"
    )
    assert out["provider"] == "nope"
    assert "hello" in out["results"][0]["title"]


async def test_no_provider_configured_uses_mock() -> None:
    """With WEB_SEARCH_PROVIDER=mock (conftest) and no override → mock results."""
    out = await WebSearchTool().run(ToolContext(session=None), query="cats")
    assert out["provider"] == "mock"
    assert out["results"] and "cats" in out["results"][0]["title"]
