"""``web_search`` tool: search the web and return top results.

Backed by an HTTP search API via :mod:`httpx`. When no API key is configured
(``WEB_SEARCH_API_KEY`` unset) or ``WEB_SEARCH_PROVIDER=mock``, it returns
deterministic mock results so the tool is fully functional offline and in CI —
no network is performed in that mode.

Supported real providers:
- ``tavily``  -> POST https://api.tavily.com/search        (free tier, no card)
- ``serper``  -> POST https://google.serper.dev/search     (free credits)
- ``serpapi`` -> GET  https://serpapi.com/search.json

**Fallback chain.** A primary and an optional fallback provider are tried in
order. If the primary errors or exhausts its free credits (HTTP 401/402/429),
the fallback is used automatically — so two free tiers can be chained to
maximise free usage. If every configured provider fails, deterministic mock
results are returned rather than failing the turn.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext

logger = get_logger(__name__)

_HTTP_TIMEOUT = 10.0
_MAX_RESULTS = 6


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
        """Run the search and return ``{query, provider, results[]}``.

        Tries the primary provider, then the optional fallback, then mock —
        moving on whenever a provider errors or runs out of free credits.
        """
        query: str = kwargs["query"]

        # Per-session config from the client (if any) wins over server env.
        ov = ctx.web_search or {}
        if ov.get("provider") or ov.get("apiKey"):
            chain = [
                ((ov.get("provider") or "").lower(), ov.get("apiKey") or None),
                (
                    (ov.get("fallbackProvider") or "").lower(),
                    ov.get("fallbackApiKey") or None,
                ),
            ]
        else:
            settings = get_settings()
            chain = [
                (settings.web_search_provider.lower(),
                 settings.web_search_api_key),
                (
                    (settings.web_search_fallback_provider or "").lower(),
                    settings.web_search_fallback_api_key,
                ),
            ]

        last_error: str | None = None
        for provider, api_key in chain:
            if not provider or provider == "mock" or not api_key:
                continue
            try:
                results = await self._search(provider, query, api_key)
            except httpx.HTTPError as exc:
                # Quota exhaustion (401/402/429) or any transport error: log and
                # let the loop try the next provider in the chain.
                last_error = f"{provider}: {exc}"
                logger.warning(
                    "web_search.provider_failed",
                    provider=provider,
                    error=str(exc),
                )
                continue
            return {"query": query, "provider": provider, "results": results}

        # No usable provider (none configured, or all exhausted) → mock.
        out: dict[str, Any] = {
            "query": query,
            "provider": "mock",
            "results": _mock_results(query),
        }
        if last_error:
            out["error"] = f"all providers failed; last: {last_error}"
        return out

    async def _search(
        self, provider: str, query: str, api_key: str
    ) -> list[dict[str, str]]:
        """Dispatch to a single provider implementation."""
        if provider == "tavily":
            return await self._tavily(query, api_key)
        if provider == "serper":
            return await self._serper(query, api_key)
        if provider == "serpapi":
            return await self._serpapi(query, api_key)
        logger.warning("web_search.unknown_provider", provider=provider)
        return _mock_results(query)

    async def _tavily(self, query: str, api_key: str) -> list[dict[str, str]]:
        """Query the Tavily search API.

        Uses the current ``Authorization: Bearer`` auth and asks Tavily for a
        synthesized ``answer`` — ideal for a spoken reply — which is surfaced as
        the first result so the model can read it out directly.
        """
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "query": query,
                    "max_results": _MAX_RESULTS,
                    "search_depth": "advanced",
                    "include_answer": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        # Real source snippets first (with publish date when present, so the
        # model can judge recency) — these are the ground truth.
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "published": item.get("published_date", ""),
            }
            for item in data.get("results", [])[:_MAX_RESULTS]
        ]
        # Tavily's synthesized answer is an AI guess — useful as a hint but it
        # can be wrong/stale for live data, so label it as a non-authoritative
        # summary and put it AFTER the real sources.
        answer = (data.get("answer") or "").strip()
        if answer:
            results.append(
                {"title": "AI summary (verify with sources)", "url": "",
                 "snippet": answer}
            )
        return results

    async def _serper(self, query: str, api_key: str) -> list[dict[str, str]]:
        """Query Serper (Google results; generous free credits, no card)."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key},
                json={"q": query, "num": _MAX_RESULTS},
            )
            resp.raise_for_status()
            data = resp.json()
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in data.get("organic", [])[:_MAX_RESULTS]
        ]
        # Serper's answer box / knowledge graph makes a great spoken reply.
        box = data.get("answerBox") or data.get("knowledgeGraph") or {}
        answer = (box.get("answer") or box.get("snippet")
                  or box.get("description") or "").strip()
        if answer:
            results.insert(0, {"title": "Answer", "url": "", "snippet": answer})
        return results

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
