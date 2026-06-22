# 📦 PROJECT HANDOFF — Landmark & Product Finder

> **Purpose of this file:** Complete context for an AI or developer picking up this
> project. Read this top-to-bottom and you will understand WHAT it does, HOW it is
> built, WHERE every piece lives, and HOW to run/extend it. No other context needed.

---

## 1. What this app does (functionality)

A small web app where a user uploads a photo (or pastes an image URL) and gets back
**structured information about it**. Three capabilities:

1. **Landmark detection** — photo of a place → landmark name, GPS coordinates,
   Google Maps link, and a Wikipedia description.
2. **Product / shopping** — photo of an object (laptop, watch, headphones, etc.) →
   identified product name, category tags, an **AI explanation** (via Gemini), and
   **search links** to marketplaces (Amazon UAE/Saudi/India/.com, Noon, Flipkart,
   eBay, AliExpress) so the user can check live prices.
3. **Free web link** — for any image URL, generate a Google Lens link (no API key
   needed) that opens full Lens results in the browser.

**Important honest limitation:** the app does NOT scrape live prices. For products it
returns marketplace **search URLs** (user clicks to see price). Real in-app prices
would need a paid shopping API (SerpApi/RapidAPI) — intentionally NOT included to keep
it free. Identification works best on clear, single, branded products / famous landmarks.

---

## 2. Tech stack

- **Backend:** Python 3 standard library `http.server` (no Flask/FastAPI). Deps:
  `httpx` (HTTP calls), `Pillow` (image resize), `rich` (CLI output only).
- **Frontend:** single static `index.html` — vanilla HTML/CSS/JS, no framework, no build.
- **External APIs:**
  - **Google Cloud Vision API** — `LANDMARK_DETECTION` and `WEB_DETECTION` features.
  - **Google Gemini API** (Generative Language API) — optional, for product AI explanation.
  - **Wikipedia REST API** — free, no key, for landmark descriptions.
- **Platform:** developed on Windows. UTF-8 stream reconfigure is applied so non-Latin
  text / emoji don't crash on cp1252 consoles.

---

## 3. Architecture & data flow

```
[Browser: index.html]                [server.py :8000]              [External APIs]
        │                                   │                              │
   user uploads image                       │                              │
   (read as base64 data URL)                │                              │
        │── POST /api/detect ──────────────▶│                              │
        │   {mode, image_data/url, keys}    │                              │
        │                          1. normalize image (resize→1280px JPEG) │
        │                          2. call Vision API ───────────────────▶ │ (Vision)
        │                             (LANDMARK or WEB detection)          │
        │                          3. (product) build marketplace links    │
        │                          4. (product) Gemini explanation ──────▶ │ (Gemini)
        │                          5. (landmark) Wikipedia detail ───────▶ │ (Wikipedia)
        │◀── JSON {ok, mode, result} ───────│                              │
   render result cards                      │                              │
```

- Frontend is a thin client: it only collects input, POSTs JSON, and renders the JSON
  response. All intelligence is in the backend.
- Backend holds NO secrets — API keys are passed per-request from the UI. (For
  production/mobile, keys should live on the server instead — see §8.)

---

## 4. File-by-file breakdown

| File | Role | Key contents |
|------|------|--------------|
| **`server.py`** | **Backend** (the brain) | HTTP server + all detection logic |
| **`index.html`** | **Frontend** | UI, image upload/preview, buttons, result rendering |
| **`landmark_finder.py`** | Helper module + standalone CLI | `build_lens_link()`, `get_wikipedia_detail()`, `WIKI_USER_AGENT`; also runnable from terminal |
| **`API.md`** | JSON API contract | Request/response schema for every mode |
| **`README.md`** | User setup guide | Install + usage |
| **`requirements.txt`** | Dependencies | `httpx`, `rich`, `Pillow` |
| **`PROJECT_HANDOFF.md`** | This file | Full project context |

### Key functions in `server.py`
- `normalize_image_b64(data_url)` → decode base64, EXIF-rotate, convert RGB, resize to
  max 1280px, re-encode JPEG. **Fixes Vision "Bad image data" on large phone photos.**
- `detect_landmarks_web(image_url, image_b64, api_key)` → Vision LANDMARK_DETECTION →
  list of landmarks (name, confidence, location, maps_url, description, wikipedia_url).
- `detect_product(image_url, image_b64, api_key, gemini_key)` → Vision WEB_DETECTION →
  product_name, categories, ai_explanation, marketplaces, matching_pages, similar_images.
- `build_marketplace_links(query)` → list of `{name, region, search_url}` per marketplace.
- `get_ai_explanation(product_name, gemini_key)` → Gemini call (tries `gemini-2.0-flash`,
  falls back to `gemini-1.5-flash`); returns None gracefully if no key / on error.
- `class Handler` → serves `index.html` on GET `/`; handles POST `/api/detect`.

### Key functions in `landmark_finder.py`
- `build_lens_link(image_url)` → `https://lens.google.com/uploadbyurl?url=...`
- `get_wikipedia_detail(name)` → Wikipedia summary `{extract, url}`. **Must send a
  descriptive `User-Agent` (WIKI_USER_AGENT)** or Wikipedia returns 403.

---

## 5. The JSON API (the contract any client uses)

Single endpoint: `POST /api/detect`, `Content-Type: application/json`.

**Universal envelope:**
```json
{ "ok": true, "mode": "web|landmark|product", "result": { ... } }
```
On error: `{ "ok": false, "error": "message" }`. Always check `ok` first.

**Requests:**
- Web:      `{ "mode": "web", "image_url": "..." }`
- Landmark: `{ "mode": "api", "api_key": "...", "image_url" | "image_data": "..." }`
- Product:  `{ "mode": "product", "api_key": "...", "gemini_key": "...(optional)", "image_url" | "image_data": "..." }`

(`image_data` = base64 data URL from a file upload; `image_url` = public image URL.)

**Result shapes:** see `API.md` for full field-by-field schema. Summary:
- Landmark result: `{ count, landmarks: [{name, confidence, location{lat,lng}, maps_url, description, wikipedia_url}] }`
- Product result: `{ product_name, categories[], ai_explanation, marketplaces[{name,region,search_url}], matching_pages[{title,url}], similar_images[] }`

Optional fields may be `null` (e.g., no Gemini key → `ai_explanation: null`).

---

## 6. How to run

```bash
pip install -r requirements.txt
python server.py
# open http://localhost:8000
```
Then in the UI: paste an image URL OR upload a file, enter API key(s), click a button.

Standalone CLI (landmark only) also exists:
```bash
python landmark_finder.py "image.jpg" --api-key VISION_KEY     # API mode
python landmark_finder.py "https://..." --web                  # free Lens link
```

---

## 7. API keys needed

| Key | For | Where to get | Cost |
|-----|-----|--------------|------|
| **Cloud Vision API key** | Landmark + Product (Vision features) | Google Cloud Console → enable "Cloud Vision API" → Credentials → API key | 1,000 images/month free, then ~$1.50/1,000 |
| **Gemini API key** | Product AI explanation (optional) | Google AI Studio / "Generative Language API" | Free tier with rate limits |

- The two keys are **separate**. A Vision-restricted key will NOT work for Gemini.
- Recommended key restriction: Application = "None" (the server calls from backend, so
  HTTP-referrer/website restrictions would break it); API = restrict to the specific API.
- Wikipedia needs no key.

---

## 8. Going to mobile / production (recommended architecture)

Chosen approach = **keep the backend**. The mobile app becomes the client (replacing
`index.html`):
1. Deploy `server.py` to a host (Render/Railway/VPS) so it has a public URL.
2. **Move API keys to the server** (env vars) instead of passing them from the client,
   so keys are never shipped inside the app. Adjust `do_POST` to read keys from env.
3. Mobile app sends the image to `POST https://your-server/api/detect` and renders the
   same JSON. The JSON contract (§5) stays identical.

---

## 9. Build history / decisions (context for why things are the way they are)

This project grew from an unrelated code review. Notable steps:
- Started as a Google-Lens-style **landmark finder**; added **product/shopping** later.
- **"Bad image data" bug:** large 4896×3712 phone photos failed Vision. Fixed by
  server-side resize/re-encode in `normalize_image_b64`.
- **Wikipedia 403:** generic User-Agent was blocked; fixed with a descriptive
  `WIKI_USER_AGENT` (Wikimedia policy).
- **UTF-8 crash:** non-Latin OCR/text crashed on Windows cp1252; fixed with
  `sys.stdout.reconfigure(encoding="utf-8")` at startup.
- **Pricing approach:** user explicitly chose FREE (identify + marketplace search links)
  over a paid live-price API.
- **Marketplaces chosen:** Gulf (Amazon.ae/.sa, Noon), India (Amazon.in, Flipkart),
  Global (Amazon.com, eBay, AliExpress).
- **Output standardized** into the `{ok, mode, result}` JSON envelope so any app can
  consume it.

---

## 10. Ideas / next steps (not yet built)
- Server-side response **caching** (same image/product → skip API call, save cost).
- **Billing alerts** on Google Cloud.
- Optional **paid shopping API** integration for real in-app prices.
- **Auth** + rate limiting if deployed publicly.
- A native **mobile client** (React Native / Android) calling the existing API.

---

*This app is self-contained in this folder. Backend = `server.py`, Frontend =
`index.html`. Everything else is helpers/docs.*
