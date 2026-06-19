"""``web_search`` tool: search the web and return top results.

Backed by an HTTP search API via :mod:`httpx`. When no API key is configured
(``WEB_SEARCH_API_KEY`` unset) or ``WEB_SEARCH_PROVIDER=mock``, it returns
deterministic mock results so the tool is fully functional offline and in CI —
no network is performed in that mode.

Supported real providers:
- ``tavily``  -> POST https://api.tavily.com/search
- ``serpapi`` -> GET  https://serpapi.com/search.json
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext

logger = get_logger(__name__)

_HTTP_TIMEOUT = 10.0
_MAX_RESULTS = 5


def _mock_results(query: str) -> list[dict[str, str]]:
    """Return deterministic placeholder results for offline/CI use."""
    return [
        {
            "title": f"Result {i + 1} for '{query}'",
            "url": f"https://example.com/search?q={query}&r={i + 1}",
            "snippet": (
                f"Placeholder offline search result {i + 1} for query "
                f"'{query}'. Configure WEB_SEARCH_API_KEY for live results."
            ),
        }
        for i in range(3)
    ]


class WebSearchTool(Tool):
    """Search the web and return the top results."""

    name = "web_search"
    description = "Search the web and return top results."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Run the search and return ``{query, provider, results[]}``."""
        query: str = kwargs["query"]
        settings = get_settings()
        provider = settings.web_search_provider.lower()
        api_key = settings.web_search_api_key

        if provider == "mock" or not api_key:
            return {
                "query": query,
                "provider": "mock",
                "results": _mock_results(query),
            }

        try:
            if provider == "tavily":
                results = await self._tavily(query, api_key)
            elif provider == "serpapi":
                results = await self._serpapi(query, api_key)
            else:
                logger.warning("web_search.unknown_provider", provider=provider)
                results = _mock_results(query)
                provider = "mock"
        except httpx.HTTPError as exc:
            # Degrade gracefully rather than failing the whole turn.
            logger.error("web_search.http_error", provider=provider, error=str(exc))
            return {
                "query": query,
                "provider": provider,
                "error": f"search failed: {exc}",
                "results": _mock_results(query),
            }

        return {"query": query, "provider": provider, "results": results}

    async def _tavily(self, query: str, api_key: str) -> list[dict[str, str]]:
        """Query the Tavily search API."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": _MAX_RESULTS,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
            }
            for item in data.get("results", [])[:_MAX_RESULTS]
        ]

    async def _serpapi(self, query: str, api_key: str) -> list[dict[str, str]]:
        """Query the SerpAPI Google search endpoint."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://serpapi.com/search.json",
                params={"q": query, "api_key": api_key, "engine": "google"},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in data.get("organic_results", [])[:_MAX_RESULTS]
        ]
