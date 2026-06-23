"""Image understanding service — landmark & product detection.

A faithful **async** port of the standalone ``landmark_finder`` app into the
FarryOn backend. The original used a blocking ``http.server`` + synchronous
``httpx``; here every network call is awaited via :class:`httpx.AsyncClient` so
it fits the FastAPI event loop, and API keys come from :class:`app.config.Settings`
(server-side) instead of the request body.

Capabilities (Google Cloud Vision):
- **landmark** — ``LANDMARK_DETECTION`` → name, GPS, Maps link, Wikipedia summary.
- **product**  — ``WEB_DETECTION`` → product name, categories, marketplace search
  links, plus an optional Gemini explanation.
- **web**      — a free Google Lens link (no key required).

Public entrypoint: :func:`run_detection`, used by both the ``POST /detect`` REST
endpoint and the ``identify_image`` agent tool. It always returns the
``{ok, mode, result}`` envelope (or ``{ok: False, error}``) and never raises for
expected failures, so callers can surface a friendly message.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from typing import Any, Literal
from urllib.parse import quote, quote_plus

import httpx

from app.config import Settings
from app.logging_conf import get_logger

logger = get_logger(__name__)

# -- Endpoints / constants ---------------------------------------------------
VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
WIKI_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
LENS_UPLOAD_BY_URL = "https://lens.google.com/uploadbyurl?url="
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Newer model first; fall back if the name 404s (Generative Language API).
# (gemini-2.0-flash / 1.5-flash now 404 on the v1beta endpoint for current keys.)
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-flash-latest"]

# Wikimedia policy requires a descriptive User-Agent (with contact) or it 403s.
WIKI_USER_AGENT = (
    "FarryOn/1.0 (https://github.com/farryon; contact: imahsanyaqoob@gmail.com)"
)

# Vision rejects very large phone photos ("Bad image data"); 1280px is plenty.
MAX_DIMENSION = 1280
_HTTP_TIMEOUT = 30.0

DetectMode = Literal["web", "landmark", "product", "auto"]

# Each marketplace's search-URL template; ``{q}`` is the URL-encoded query.
MARKETPLACES: list[dict[str, str]] = [
    {"name": "Amazon UAE", "region": "Gulf", "tpl": "https://www.amazon.ae/s?k={q}"},
    {"name": "Amazon Saudi", "region": "Gulf", "tpl": "https://www.amazon.sa/s?k={q}"},
    {"name": "Noon UAE", "region": "Gulf", "tpl": "https://www.noon.com/uae-en/search/?q={q}"},
    {"name": "Noon Saudi", "region": "Gulf", "tpl": "https://www.noon.com/saudi-en/search/?q={q}"},
    {"name": "Amazon India", "region": "India", "tpl": "https://www.amazon.in/s?k={q}"},
    {"name": "Flipkart", "region": "India", "tpl": "https://www.flipkart.com/search?q={q}"},
    {"name": "Amazon.com", "region": "Global", "tpl": "https://www.amazon.com/s?k={q}"},
    {"name": "eBay", "region": "Global", "tpl": "https://www.ebay.com/sch/i.html?_nkw={q}"},
    {"name": "AliExpress", "region": "Global", "tpl": "https://www.aliexpress.com/wholesale?SearchText={q}"},
]


class DetectionError(Exception):
    """A user-facing detection failure (bad image, API error, no key)."""


# -- Pure helpers ------------------------------------------------------------

def build_lens_link(image_url: str) -> str:
    """Build a Google Lens 'search by image URL' link (free, no key)."""
    return LENS_UPLOAD_BY_URL + quote(image_url, safe="")


def build_marketplace_links(query: str) -> list[dict[str, str]]:
    """Return ``{name, region, search_url}`` for each configured marketplace."""
    q = quote_plus(query)
    return [
        {"name": m["name"], "region": m["region"], "search_url": m["tpl"].format(q=q)}
        for m in MARKETPLACES
    ]


def normalize_image_b64(data_url_or_b64: str) -> str:
    """Decode, EXIF-rotate, convert to RGB, downscale ≤1280px, re-encode JPEG.

    Fixes Vision "Bad image data" on large/odd-format phone photos. Pure CPU
    work; callers should run it off the event loop (see :func:`_normalize`).
    """
    from PIL import Image, ImageOps  # local import keeps Pillow optional at import time

    b64 = data_url_or_b64
    if "," in b64:  # strip a ``data:image/...;base64,`` prefix if present
        b64 = b64.split(",", 1)[1]
    raw = base64.b64decode(b64)

    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # honour phone rotation
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode("utf-8")


async def _normalize(image_b64: str) -> str:
    """Run :func:`normalize_image_b64` in a thread (Pillow is blocking)."""
    return await asyncio.to_thread(normalize_image_b64, image_b64)


async def _fetch_b64(client: httpx.AsyncClient, url: str) -> str | None:
    """Download an image URL and return normalized base64 (for Gemini inline)."""
    try:
        r = await client.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            return None
        return await _normalize(base64.b64encode(r.content).decode("utf-8"))
    except Exception:  # noqa: BLE001 - best-effort
        return None


async def _async_none() -> None:
    return None


async def _async_empty_landmarks() -> dict[str, Any]:
    return {"count": 0, "landmarks": []}


async def _build_image_req(
    image_url: str | None, image_b64: str | None
) -> dict[str, Any]:
    """Build Vision's ``image`` request object from a URL or base64 payload.

    Raises :class:`DetectionError` when no image is supplied or it cannot be read.
    """
    if image_url:
        return {"source": {"imageUri": image_url}}
    if image_b64:
        try:
            return {"content": await _normalize(image_b64)}
        except Exception as exc:  # noqa: BLE001 - report any decode failure
            raise DetectionError(
                f"Couldn't read the image (bad/corrupt format?): {exc}"
            ) from exc
    raise DetectionError("No image provided.")


async def _vision_annotate(
    client: httpx.AsyncClient,
    image_req: dict[str, Any],
    feature: str,
    api_key: str,
    *,
    max_results: int,
) -> dict[str, Any]:
    """POST one Vision ``images:annotate`` request and return ``responses[0]``."""
    payload = {
        "requests": [
            {"image": image_req, "features": [{"type": feature, "maxResults": max_results}]}
        ]
    }
    try:
        r = await client.post(
            f"{VISION_ENDPOINT}?key={api_key}", json=payload, timeout=_HTTP_TIMEOUT
        )
    except httpx.RequestError as exc:
        raise DetectionError(f"Network error: {exc}") from exc

    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:  # noqa: BLE001
            msg = r.text
        raise DetectionError(f"Vision API error ({r.status_code}): {msg}")

    resp = r.json()["responses"][0]
    if "error" in resp:
        raise DetectionError(resp["error"].get("message", "API error"))
    return resp


# -- Enrichment --------------------------------------------------------------

async def get_wikipedia_detail(
    client: httpx.AsyncClient, name: str
) -> dict[str, Any] | None:
    """Fetch a Wikipedia summary ``{extract, url}`` for a landmark name."""
    try:
        url = WIKI_SUMMARY_API + quote(name.replace(" ", "_"))
        r = await client.get(
            url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": WIKI_USER_AGENT},
        )
        if r.status_code == 200:
            d = r.json()
            return {
                "extract": d.get("extract"),
                "url": (d.get("content_urls", {}) or {})
                .get("desktop", {})
                .get("page"),
            }
    except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
        logger.warning("vision.wiki_failed", name=name, error=str(exc))
    return None


async def get_ai_explanation(
    client: httpx.AsyncClient,
    product_name: str,
    gemini_key: str | None,
    lang: str | None = None,
) -> str | None:
    """Ask Gemini for a short product explanation (``None`` on miss).

    The reply is written in the user's language: ``lang`` is a BCP-47 code
    (e.g. ``en``, ``ar``, ``hi``, ``ur``) — typically the device locale from the
    Finder screen. For Hindi/Urdu the model uses Roman (Latin) script. Defaults
    to English.
    """
    if not gemini_key or not product_name:
        return None
    language = lang or "en"
    prompt = (
        "You are a helpful shopping assistant. Explain the product below to the "
        f"user in their language (BCP-47 code: '{language}'). If that language is "
        "Hindi or Urdu, write it in Roman (Latin) script, not Devanagari/Arabic "
        "script. Format:\n"
        "- What it is (1 line)\n"
        "- 3-4 key features (bullet points)\n"
        "- Who it's best for (1 line)\n"
        "Keep it short and clear; no marketing fluff.\n\n"
        f"Product: {product_name}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    for model in GEMINI_MODELS:
        try:
            r = await client.post(
                f"{GEMINI_BASE}/{model}:generateContent?key={gemini_key}",
                json=body,
                timeout=_HTTP_TIMEOUT,
            )
            # Renamed (404) or transient/throttled (429/5xx) — try the next
            # model rather than giving up on the (optional) explanation.
            if r.status_code in (404, 429, 500, 502, 503):
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as exc:  # noqa: BLE001 - explanation is optional
            logger.warning("vision.gemini_failed", model=model, error=str(exc))
            return None
    return None


# Kinds Gemini may return that we present as a "place / landmark" card.
_PLACE_KINDS = {
    "landmark", "building", "place", "monument", "nature", "art",
    "interior", "structure", "scene",
}


def maps_search_url(query: str) -> str:
    """A Google Maps SEARCH link for a place name (no exact GPS needed)."""
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query)


async def gemini_vision_identify(
    client: httpx.AsyncClient,
    image_b64: str,
    gemini_key: str | None,
    lang: str | None = None,
) -> dict[str, Any] | None:
    """Identify the main subject of an image with Gemini's multimodal model.

    Unlike Google Vision's narrow LANDMARK_DETECTION (famous places only) or
    WEB_DETECTION (often generic), Gemini can name and richly describe *anything*
    — a local shop, a building, a gadget, food, a plant — which is what makes
    the Finder feel smart. Returns a parsed dict
    ``{kind, name, description, where, details[]}`` or ``None``.
    """
    if not gemini_key or not image_b64:
        return None
    language = lang or "en"
    prompt = (
        "Identify the MAIN subject of this image. Reply with ONLY compact JSON "
        "(no markdown), exactly these keys:\n"
        '{"kind":"landmark|building|place|monument|nature|product|object|food|'
        'plant|animal|art|other",'
        '"name":"the most specific real name you can give",'
        '"description":"2-4 informative, accurate sentences",'
        '"where":"city and country if it is a place/landmark, else null",'
        '"details":["3-5 short key facts, specs, or notable points"]}\n'
        f"Write description and details in the user's language (BCP-47 "
        f"'{language}'); for Hindi or Urdu use Roman (Latin) script. Be "
        "specific and factual. If you are not sure of the exact name, give your "
        "best identification and say so briefly in the description."
    )
    body = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    for model in GEMINI_MODELS:
        try:
            r = await client.post(
                f"{GEMINI_BASE}/{model}:generateContent?key={gemini_key}",
                json=body,
                timeout=_HTTP_TIMEOUT,
            )
            if r.status_code in (404, 429, 500, 502, 503):
                continue
            if r.status_code != 200:
                return None
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            return data if isinstance(data, dict) and data.get("name") else None
        except Exception as exc:  # noqa: BLE001 - vision identify is best-effort
            logger.warning("vision.gemini_vision_failed", model=model, error=str(exc))
            continue
    return None


def _gem_description(gem: dict[str, Any]) -> str:
    """Compose a readable description from Gemini's fields."""
    parts: list[str] = []
    if gem.get("description"):
        parts.append(str(gem["description"]).strip())
    where = gem.get("where")
    if where and str(where).lower() not in ("null", "none", ""):
        parts.append(f"Location: {where}.")
    details = gem.get("details") or []
    if isinstance(details, list) and details:
        parts.append(" ".join(f"• {d}" for d in details[:5]))
    return "\n".join(parts).strip()


async def _gemini_place_landmark(
    client: httpx.AsyncClient, gem: dict[str, Any]
) -> dict[str, Any]:
    """Turn a Gemini place/landmark identification into a landmark card.

    No exact GPS (Gemini can't give coordinates), but we add a Maps SEARCH link
    for the name + city, and a Wikipedia link if the name has an article.
    """
    name = str(gem.get("name") or "Unknown place")
    where = gem.get("where")
    query = name if not where or str(where).lower() in (
        "null", "none", ""
    ) else f"{name}, {where}"
    wiki = await get_wikipedia_detail(client, name)
    desc = _gem_description(gem)
    if wiki and wiki.get("extract"):
        desc = f"{desc}\n\n{wiki['extract']}".strip()
    return {
        "name": name,
        "confidence": 0.9,  # Gemini gave a confident identification
        "location": None,
        "maps_url": maps_search_url(query),
        "description": desc or None,
        "wikipedia_url": (wiki or {}).get("url"),
    }


# -- Detection ---------------------------------------------------------------

async def _wiki_or_none(
    client: httpx.AsyncClient, name: str | None
) -> dict[str, Any] | None:
    """Wikipedia detail for ``name`` (``None`` for an empty name)."""
    return await get_wikipedia_detail(client, name) if name else None


async def detect_landmarks(
    client: httpx.AsyncClient,
    *,
    image_req: dict[str, Any],
    api_key: str,
    max_results: int = 5,
) -> dict[str, Any]:
    """Detect landmarks and enrich each with a Wikipedia summary.

    Wikipedia lookups run concurrently so a multi-landmark frame costs roughly
    one round-trip rather than one per landmark (which could blow the tool
    timeout on e.g. a skyline).
    """
    resp = await _vision_annotate(
        client, image_req, "LANDMARK_DETECTION", api_key, max_results=max_results
    )

    anns = resp.get("landmarkAnnotations", [])
    wikis = await asyncio.gather(
        *(_wiki_or_none(client, lm.get("description")) for lm in anns)
    )

    landmarks: list[dict[str, Any]] = []
    for lm, wiki in zip(anns, wikis):
        loc = (lm.get("locations") or [{}])[0].get("latLng", {})
        lat, lng = loc.get("latitude"), loc.get("longitude")
        has_loc = lat is not None and lng is not None
        landmarks.append(
            {
                "name": lm.get("description"),
                "confidence": round(lm.get("score", 0.0), 4),
                "location": {"lat": lat, "lng": lng} if has_loc else None,
                "maps_url": (
                    f"https://www.google.com/maps?q={lat},{lng}" if has_loc else None
                ),
                "description": (wiki or {}).get("extract"),
                "wikipedia_url": (wiki or {}).get("url"),
            }
        )
    return {"count": len(landmarks), "landmarks": landmarks}


async def detect_product(
    client: httpx.AsyncClient,
    *,
    image_req: dict[str, Any],
    api_key: str,
    gemini_key: str | None = None,
    lang: str | None = None,
    gem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Identify a product via Gemini vision (primary) + web detection (links).

    Gemini's image identification gives the best name + a rich explanation;
    Google's WEB_DETECTION adds matching pages / similar images and a fallback
    name. Marketplace links are built from the chosen name.
    """
    resp = await _vision_annotate(
        client, image_req, "WEB_DETECTION", api_key, max_results=10
    )

    web = resp.get("webDetection", {})
    best = web.get("bestGuessLabels", [])
    entities = [
        e["description"] for e in web.get("webEntities", []) if e.get("description")
    ]
    web_query = (best[0].get("label") if best else None) or (
        entities[0] if entities else None
    )

    # Prefer Gemini's name (far more specific than WEB_DETECTION's labels).
    gem_name = (gem or {}).get("name")
    query = gem_name or web_query

    # Explanation: prefer Gemini's vision-based description; else a text one.
    if gem:
        explanation = _gem_description(gem)
    else:
        explanation = (
            await get_ai_explanation(client, query, gemini_key, lang)
            if query else None
        )

    # Categories: Gemini details first, then web entities (deduped).
    gem_details = (gem or {}).get("details") or []
    categories: list[str] = []
    for c in [*(gem_details if isinstance(gem_details, list) else []), *entities]:
        c = str(c).strip()
        if c and c not in categories:
            categories.append(c)

    pages = [
        {"title": p.get("pageTitle", "(no title)"), "url": p.get("url")}
        for p in web.get("pagesWithMatchingImages", [])[:6]
        if p.get("url")
    ]
    similar = [
        i["url"] for i in web.get("visuallySimilarImages", [])[:6] if i.get("url")
    ]

    return {
        "product_name": query,
        "categories": categories[:8],
        "ai_explanation": explanation or None,
        "marketplaces": build_marketplace_links(query) if query else [],
        "matching_pages": pages,
        "similar_images": similar,
    }


# -- Public orchestration ----------------------------------------------------

async def run_detection(
    mode: DetectMode,
    *,
    settings: Settings,
    image_data: str | None = None,
    image_url: str | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    """Run detection for ``mode`` and return the ``{ok, mode, result}`` envelope.

    ``mode`` of ``"auto"`` tries landmark detection first and falls back to
    product detection when no landmark is found — handy for the voice tool where
    the user hasn't specified which kind of thing they're pointing at.

    Keys are read from ``settings`` (never the caller). Expected failures return
    ``{ok: False, error}`` rather than raising, so callers can show a friendly
    message; only programming errors propagate.
    """
    vision_key = settings.vision_api_key
    gemini_key = settings.gemini_api_key

    try:
        if mode == "web":
            if not image_url:
                raise DetectionError("Web mode needs an image URL.")
            return {"ok": True, "mode": "web", "result": {"lens_url": build_lens_link(image_url)}}

        if not vision_key:
            raise DetectionError(
                "Vision API key is not set (add VISION_API_KEY on the server)."
            )

        if mode not in ("landmark", "product", "auto"):
            raise DetectionError(f"Unknown mode: {mode}")

        # Decode/normalize the image ONCE and share it across detections — in
        # `auto` mode both landmark and product detection use the same payload.
        image_req = await _build_image_req(image_url, image_data)

        async with httpx.AsyncClient() as client:
            # Base64 for Gemini's multimodal call (needs inline bytes, not a URL).
            img_b64 = image_req.get("content")
            if img_b64 is None and image_url:
                img_b64 = await _fetch_b64(client, image_url)

            run_lm = mode in ("landmark", "auto")
            # Gemini "sees" the image (identifies anything); Google landmark
            # detection runs alongside for precise GPS on famous landmarks.
            gem, google_lm = await asyncio.gather(
                gemini_vision_identify(client, img_b64, gemini_key, lang)
                if img_b64 else _async_none(),
                detect_landmarks(client, image_req=image_req, api_key=vision_key)
                if run_lm else _async_empty_landmarks(),
            )

            # 1) Google recognised a famous landmark → precise GPS + Wikipedia,
            #    enriched with Gemini's richer description.
            if google_lm["count"] > 0:
                lm0 = google_lm["landmarks"][0]
                extra = _gem_description(gem) if gem else ""
                if extra:
                    lm0["description"] = (
                        f"{extra}\n\n{lm0['description']}".strip()
                        if lm0.get("description") else extra
                    )
                return {"ok": True, "mode": "landmark", "result": google_lm}

            # 2) Gemini says it's a place/landmark → landmark card with rich
            #    detail + a Maps search link, even for non-famous places.
            if run_lm and gem and gem.get("kind") in _PLACE_KINDS:
                entry = await _gemini_place_landmark(client, gem)
                return {
                    "ok": True, "mode": "landmark",
                    "result": {"count": 1, "landmarks": [entry]},
                }

            # 3) Otherwise it's a product/object → product card (Gemini name +
            #    explanation, Google web links + marketplaces).
            product = await detect_product(
                client, image_req=image_req, api_key=vision_key,
                gemini_key=gemini_key, lang=lang, gem=gem,
            )
            return {"ok": True, "mode": "product", "result": product}
    except DetectionError as exc:
        logger.info("vision.detect_failed", mode=mode, error=str(exc))
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - never break the {ok,...} contract
        # The standalone server wrapped its whole dispatch in a catch-all so a
        # malformed-but-200 API response (or any unexpected error) still became a
        # friendly envelope rather than a 500. Preserve that invariant here.
        logger.error("vision.detect_error", mode=mode, error=repr(exc))
        return {
            "ok": False,
            "error": "Something went wrong during detection. Please try again shortly.",
        }
