"""Public marketing site + direct APK download.

Serves the landing page at ``/`` and streams the Android APK from a directory of
prebuilt builds, so users can install FarryOn straight from the website with no
Play Store. The build directory is resolved (in order):

1. ``settings.apk_dir`` if set (production: point it at where the APKs live);
2. the repo's top-level ``apk/`` folder, found relative to this file (local dev).

Builds are matched by ABI: ``arm64`` (most modern phones) and ``arm32``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.config import get_settings
from app.logging_conf import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["site"])

_HERE = Path(__file__).resolve().parent
_INDEX = _HERE / "index.html"

# Public app version shown on the site and in the download filename.
APP_VERSION = "1.0.0"

# The landing page is a self-contained HTML document with inline CSS/JS. The
# app-wide default CSP is `default-src 'none'` (correct for the JSON API, blanks
# an HTML page), so this route sets its own page-appropriate policy. The
# SecurityHeadersMiddleware uses setdefault, so this value wins. Everything the
# page loads is same-origin or an inline style/script or a data: image.
_SITE_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

# ABI slug -> the APK filename to serve for it. These are the shipped "Aurora"
# builds in the repo's apk/ directory.
_BUILDS: dict[str, str] = {
    "arm64": "FarryOn-Aurora-arm64.apk",
    "arm32": "FarryOn-Aurora-arm32.apk",
}


def _apk_dir() -> Path:
    """Directory holding the prebuilt APKs (settings override, else repo apk/)."""
    configured = getattr(get_settings(), "apk_dir", None)
    if configured:
        return Path(configured)
    # app/web/router.py -> parents: [web, app, backend, <repo root>]
    return _HERE.parents[2] / "apk"


def _build_path(abi: str) -> Path | None:
    """Resolve the APK path for an ABI, or None if the file is missing."""
    name = _BUILDS.get(abi)
    if not name:
        return None
    path = _apk_dir() / name
    return path if path.is_file() else None


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing() -> HTMLResponse:
    """The public marketing / download page."""
    try:
        return HTMLResponse(
            _INDEX.read_text(encoding="utf-8"),
            headers={"Content-Security-Policy": _SITE_CSP},
        )
    except OSError as exc:  # pragma: no cover - only if the asset is missing
        logger.warning("site.index_missing", error=str(exc))
        raise HTTPException(status_code=404, detail="site not found") from exc


@router.get("/farry-icon.png", include_in_schema=False)
@router.get("/favicon.ico", include_in_schema=False)
async def brand_icon() -> FileResponse:
    """The FarryOn winged-orb app icon — used as the site logo and favicon."""
    icon = _HERE / "farry-icon.png"
    if not icon.is_file():  # pragma: no cover - asset ships with the package
        raise HTTPException(status_code=404, detail="icon not found")
    return FileResponse(icon, media_type="image/png")


@router.get("/download/info", include_in_schema=False)
async def download_info() -> JSONResponse:
    """Live build metadata for the page (version + per-ABI availability/size)."""
    out: dict[str, object] = {"version": APP_VERSION}
    for abi in _BUILDS:
        path = _build_path(abi)
        out[abi] = (
            {"available": True, "size": path.stat().st_size}
            if path
            else {"available": False, "size": None}
        )
    return JSONResponse(out)


@router.get("/download", include_in_schema=False)
@router.get("/download/{abi}", include_in_schema=False)
async def download_apk(abi: str = "arm64") -> FileResponse:
    """Stream the APK for ``abi`` (defaults to arm64) as a file download.

    404s cleanly when the ABI is unknown or the build isn't present, so the
    button can degrade rather than 500.
    """
    if abi not in _BUILDS:
        raise HTTPException(status_code=404, detail=f"unknown build: {abi}")
    path = _build_path(abi)
    if path is None:
        logger.warning("site.apk_missing", abi=abi, dir=str(_apk_dir()))
        raise HTTPException(status_code=404, detail="build not available yet")
    filename = f"FarryOn-{APP_VERSION}-{abi}.apk"
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=filename,
    )
