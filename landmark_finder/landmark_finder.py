#!/usr/bin/env python3
"""
Landmark Finder — kisi bhi landmark image se detail nikalta hai.

Do tarike (dono implemented):
  1. WEB mode  : 100% free, koi API key nahi. Image URL ka Google Lens link
                 bana deta hai — link kholte hi poori detail browser me.
  2. API mode  : Google Cloud Vision (LANDMARK_DETECTION) se landmark ka naam,
                 confidence, coordinates aur Google Maps link. Phir Wikipedia
                 se uski poori detail (description) bhi nikalta hai.

Usage:
  # Web mode (free) — web image URL ke liye
  python landmark_finder.py "https://example.com/photo.jpg" --web

  # API mode — automated detail (Cloud Vision API key chahiye)
  python landmark_finder.py "photo.jpg" --api-key YOUR_KEY
  python landmark_finder.py "https://example.com/photo.jpg"   # key env me ho to

API key env var: GOOGLE_VISION_API_KEY
"""
import argparse
import base64
import os
import sys
from urllib.parse import quote

import httpx
from rich.console import Console
from rich.panel import Panel

# Windows par UTF-8 output force karo, warna emoji/non-Latin text cp1252 par crash karta hai.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding="utf-8")
            except Exception:
                pass

console = Console()

# Wikimedia ki policy ke mutabik proper User-Agent (contact ke saath) zaroori hai.
WIKI_USER_AGENT = "LandmarkFinder/1.0 (https://github.com/landmark-finder; contact@example.com)"

VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
WIKI_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
LENS_UPLOAD_BY_URL = "https://lens.google.com/uploadbyurl?url="


def is_url(s: str) -> bool:
    return s.lower().startswith(("http://", "https://"))


# ---------------------------------------------------------------------------
# 1) WEB MODE — free, no key
# ---------------------------------------------------------------------------
def build_lens_link(image_url: str) -> str:
    """Google Lens 'search by image URL' link banata hai."""
    return LENS_UPLOAD_BY_URL + quote(image_url, safe="")


def run_web_mode(image: str):
    if not is_url(image):
        console.print(
            "[bold red]Web mode ke liye image ek public URL honi chahiye[/bold red] "
            "(local file ka koi public link nahi hota)."
        )
        console.print(
            "Local file hai to: pehle kahin upload karein, ya API mode use karein."
        )
        sys.exit(1)

    link = build_lens_link(image)
    console.print(
        Panel.fit(
            f"[bold green]Google Lens Link (free):[/bold green]\n{link}\n\n"
            "[dim]Is link ko browser me kholein — Google Lens image ko pehchan kar\n"
            "landmark, similar images aur poori detail dikha dega.[/dim]",
            title="🌐 WEB MODE",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# Wikipedia enrichment (free, no key) — landmark ki poori detail
# ---------------------------------------------------------------------------
def get_wikipedia_detail(name: str):
    try:
        url = WIKI_SUMMARY_API + quote(name.replace(" ", "_"))
        r = httpx.get(
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
    except Exception as e:
        return {"error": str(e)}
    return None


# ---------------------------------------------------------------------------
# 2) API MODE — Cloud Vision landmark detection
# ---------------------------------------------------------------------------
def detect_landmarks(image: str, api_key: str, max_results: int = 5):
    if is_url(image):
        image_request = {"source": {"imageUri": image}}
    else:
        if not os.path.isfile(image):
            console.print(f"[bold red]File nahi mili:[/bold red] {image}")
            sys.exit(1)
        with open(image, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")
        image_request = {"content": content}

    payload = {
        "requests": [
            {
                "image": image_request,
                "features": [
                    {"type": "LANDMARK_DETECTION", "maxResults": max_results}
                ],
            }
        ]
    }

    try:
        r = httpx.post(
            f"{VISION_ENDPOINT}?key={api_key}", json=payload, timeout=30
        )
    except httpx.RequestError as e:
        console.print(f"[bold red]Network error:[/bold red] {e}")
        sys.exit(1)

    if r.status_code != 200:
        msg = r.json().get("error", {}).get("message", r.text)
        console.print(f"[bold red]Vision API error ({r.status_code}):[/bold red] {msg}")
        sys.exit(1)

    resp = r.json()["responses"][0]
    if "error" in resp:
        console.print(f"[bold red]API error:[/bold red] {resp['error'].get('message')}")
        sys.exit(1)

    results = []
    for lm in resp.get("landmarkAnnotations", []):
        loc = (lm.get("locations") or [{}])[0].get("latLng", {})
        results.append(
            {
                "name": lm.get("description"),
                "score": lm.get("score", 0.0),
                "lat": loc.get("latitude"),
                "lng": loc.get("longitude"),
            }
        )
    return results


def run_api_mode(image: str, api_key: str):
    console.print("[cyan]Cloud Vision se landmark detect kar raha hoon...[/cyan]")
    landmarks = detect_landmarks(image, api_key)

    if not landmarks:
        console.print(
            Panel.fit(
                "Koi landmark nahi pehchana ja saka.\n"
                "[dim]Ho sakta hai image me clear landmark na ho, ya famous na ho.[/dim]",
                title="❌ Result",
                border_style="yellow",
            )
        )
        return

    for i, lm in enumerate(landmarks, 1):
        name = lm["name"]
        lines = [f"[bold]{name}[/bold]"]
        lines.append(f"Confidence : {lm['score'] * 100:.1f}%")

        if lm["lat"] is not None and lm["lng"] is not None:
            lat, lng = lm["lat"], lm["lng"]
            maps = f"https://www.google.com/maps?q={lat},{lng}"
            lines.append(f"📍 Location : {lat:.5f}, {lng:.5f}")
            lines.append(f"🗺️  Maps     : {maps}")

        wiki = get_wikipedia_detail(name)
        if wiki and wiki.get("extract"):
            lines.append(f"\n📖 About:\n{wiki['extract']}")
            if wiki.get("url"):
                lines.append(f"\n🔗 Wikipedia: {wiki['url']}")

        console.print(
            Panel.fit(
                "\n".join(lines),
                title=f"🏛️  Landmark #{i}",
                border_style="green",
            )
        )


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Kisi bhi landmark image se detail nikaalo."
    )
    parser.add_argument("image", help="Image file path ya URL")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Web mode (free, no key) — Google Lens link banata hai.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GOOGLE_VISION_API_KEY"),
        help="Cloud Vision API key (ya GOOGLE_VISION_API_KEY env var).",
    )
    args = parser.parse_args()

    # Mode chunav:
    #  --web diya  -> web mode
    #  api key hai -> api mode (automated detail)
    #  warna       -> web mode par fallback (key ke bina)
    if args.web:
        run_web_mode(args.image)
    elif args.api_key:
        run_api_mode(args.image, args.api_key)
    else:
        console.print(
            "[yellow]Koi API key nahi mili — web mode (free) use kar raha hoon.[/yellow]"
        )
        console.print(
            "[dim]Automated detail (text) chahiye to --api-key dein ya "
            "GOOGLE_VISION_API_KEY set karein.[/dim]\n"
        )
        run_web_mode(args.image)


if __name__ == "__main__":
    main()
