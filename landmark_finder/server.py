#!/usr/bin/env python3
"""
Landmark Finder ka web server (Python stdlib — koi extra install nahi).

Chalao:
    python server.py
Phir browser me kholo: http://localhost:8000
"""
import base64
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote_plus

import httpx
from PIL import Image, ImageOps

# landmark_finder.py se logic reuse karte hain
from landmark_finder import (
    WIKI_USER_AGENT,
    build_lens_link,
    get_wikipedia_detail,
)

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        _rc = getattr(_s, "reconfigure", None)
        if _rc:
            try:
                _rc(encoding="utf-8")
            except Exception:
                pass

HERE = os.path.dirname(os.path.abspath(__file__))
VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
PORT = int(os.environ.get("PORT", 8000))
MAX_DIMENSION = 1280  # Vision ke liye itna kaafi hai; badi image "Bad image data" deti hai


def normalize_image_b64(data_url_or_b64: str) -> str:
    """Base64/data-URL image ko decode karke resize + clean JPEG re-encode karta hai.
    Isse badi/odd-format images par Vision ka 'Bad image data' error theek ho jata hai."""
    b64 = data_url_or_b64
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    raw = base64.b64decode(b64)

    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # phone rotation theek karo
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode("utf-8")


def detect_landmarks_web(image_url=None, image_b64=None, api_key=None, max_results=5):
    """URL ya base64 image se Cloud Vision landmark detection. Errors string me return."""
    if image_url:
        image_req = {"source": {"imageUri": image_url}}
    elif image_b64:
        try:
            clean_b64 = normalize_image_b64(image_b64)
        except Exception as e:
            return None, f"Image padhi nahi ja saki (format/corrupt?): {e}"
        image_req = {"content": clean_b64}
    else:
        return None, "Koi image nahi mili."

    payload = {
        "requests": [
            {
                "image": image_req,
                "features": [{"type": "LANDMARK_DETECTION", "maxResults": max_results}],
            }
        ]
    }
    try:
        r = httpx.post(f"{VISION_ENDPOINT}?key={api_key}", json=payload, timeout=30)
    except httpx.RequestError as e:
        return None, f"Network error: {e}"

    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        return None, f"Vision API error ({r.status_code}): {msg}"

    resp = r.json()["responses"][0]
    if "error" in resp:
        return None, resp["error"].get("message", "API error")

    landmarks = []
    for lm in resp.get("landmarkAnnotations", []):
        loc = (lm.get("locations") or [{}])[0].get("latLng", {})
        name = lm.get("description")
        wiki = get_wikipedia_detail(name) if name else None
        lat, lng = loc.get("latitude"), loc.get("longitude")
        has_loc = lat is not None and lng is not None
        landmarks.append(
            {
                "name": name,
                "confidence": round(lm.get("score", 0.0), 4),
                "location": {"lat": lat, "lng": lng} if has_loc else None,
                "maps_url": (
                    f"https://www.google.com/maps?q={lat},{lng}" if has_loc else None
                ),
                "description": (wiki or {}).get("extract"),
                "wikipedia_url": (wiki or {}).get("url"),
            }
        )
    return landmarks, None


# ---------------------------------------------------------------------------
# PRODUCT / SHOPPING — Vision WEB_DETECTION se pehchano + marketplace search links
# ---------------------------------------------------------------------------
# Har marketplace ka search-URL template. {q} jagah par product naam aata hai.
MARKETPLACES = [
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


def build_marketplace_links(query: str):
    q = quote_plus(query)
    return [
        {"name": m["name"], "region": m["region"], "search_url": m["tpl"].format(q=q)}
        for m in MARKETPLACES
    ]


# Gemini se product ke baare me AI explanation. Naye model pehle, fallback baad me.
GEMINI_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash"]
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def get_ai_explanation(product_name: str, gemini_key: str):
    """Gemini se product ka simple Hinglish explanation. Key na ho/error ho to None."""
    if not gemini_key or not product_name:
        return None
    prompt = (
        "Tum ek helpful shopping assistant ho. Niche diye product ke baare me user ko "
        "simple Hinglish (Roman Hindi) me samjhao. Format:\n"
        "- Ye kya hai (1 line)\n"
        "- 3-4 key features (bullet points)\n"
        "- Kiske liye best hai (1 line)\n"
        "Short aur clear rakho, marketing-bakwaas mat karo.\n\n"
        f"Product: {product_name}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    for model in GEMINI_MODELS:
        try:
            r = httpx.post(
                f"{GEMINI_BASE}/{model}:generateContent?key={gemini_key}",
                json=body,
                timeout=30,
            )
            if r.status_code == 404:
                continue  # model naam badal gaya ho to agla try karo
            if r.status_code != 200:
                return None
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            return None
    return None


def _build_image_req(image_url=None, image_b64=None):
    """URL ya base64 se Vision ka 'image' request banata hai. (req, error) return."""
    if image_url:
        return {"source": {"imageUri": image_url}}, None
    if image_b64:
        try:
            return {"content": normalize_image_b64(image_b64)}, None
        except Exception as e:
            return None, f"Image padhi nahi ja saki (format/corrupt?): {e}"
    return None, "Koi image nahi mili."


def detect_product(image_url=None, image_b64=None, api_key=None, gemini_key=None):
    """Vision WEB_DETECTION se product pehchano, marketplace links + AI explanation."""
    image_req, err = _build_image_req(image_url, image_b64)
    if err:
        return None, err

    payload = {
        "requests": [
            {"image": image_req, "features": [{"type": "WEB_DETECTION", "maxResults": 10}]}
        ]
    }
    try:
        r = httpx.post(f"{VISION_ENDPOINT}?key={api_key}", json=payload, timeout=30)
    except httpx.RequestError as e:
        return None, f"Network error: {e}"

    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        return None, f"Vision API error ({r.status_code}): {msg}"

    resp = r.json()["responses"][0]
    if "error" in resp:
        return None, resp["error"].get("message", "API error")

    web = resp.get("webDetection", {})
    best = web.get("bestGuessLabels", [])
    entities = [
        e["description"] for e in web.get("webEntities", []) if e.get("description")
    ]
    # Search query: pehle best-guess label, warna top web entity
    query = best[0]["label"] if best else (entities[0] if entities else None)

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
        "categories": entities[:6],
        "ai_explanation": get_ai_explanation(query, gemini_key) if query else None,
        "marketplaces": build_marketplace_links(query) if query else [],
        "matching_pages": pages,
        "similar_images": similar,
    }, None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet logs
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "index.html not found")
        else:
            self._send(404, json.dumps({"ok": False, "error": "Not found"}))

    def do_POST(self):
        if self.path != "/api/detect":
            self._send(404, json.dumps({"ok": False, "error": "Not found"}))
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or "{}")
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "error": f"Bad request: {e}"}))
            return

        mode = req.get("mode")
        try:
            if mode == "web":
                url = req.get("image_url")
                if not url:
                    raise ValueError("Web mode ke liye image URL chahiye.")
                result = {"lens_url": build_lens_link(url)}
            elif mode == "api":
                if not req.get("api_key"):
                    raise ValueError("API key chahiye.")
                landmarks, err = detect_landmarks_web(
                    image_url=req.get("image_url"),
                    image_b64=req.get("image_data"),
                    api_key=req.get("api_key"),
                )
                if err:
                    raise ValueError(err)
                result = {"landmarks": landmarks, "count": len(landmarks)}
                mode = "landmark"  # response me saaf naam
            elif mode == "product":
                if not req.get("api_key"):
                    raise ValueError("API key chahiye.")
                product, err = detect_product(
                    image_url=req.get("image_url"),
                    image_b64=req.get("image_data"),
                    api_key=req.get("api_key"),
                    gemini_key=req.get("gemini_key"),
                )
                if err:
                    raise ValueError(err)
                result = product
            else:
                raise ValueError("Unknown mode.")
            self._send(200, json.dumps({"ok": True, "mode": mode, "result": result}))
        except ValueError as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)}))
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": f"Server error: {e}"}))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"\n  🏛️  Landmark Finder chal raha hai!")
    print(f"  👉 Browser me kholo:  http://localhost:{PORT}\n")
    print("  (Band karne ke liye Ctrl+C)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server band ho gaya.")
        server.shutdown()


if __name__ == "__main__":
    main()
