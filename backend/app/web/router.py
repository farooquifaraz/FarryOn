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


# ---- auth email landing pages ---------------------------------------------
# The verification / reset emails need somewhere CLICKABLE to land. The admin
# SPA has no such routes, so these two self-contained pages live with the
# marketing site: their inline JS POSTs the token to the JSON API on the same
# origin (allowed by _SITE_CSP's connect-src 'self') and shows the outcome.
# NOTE: both paths must be listed in admin/Caddyfile's @backend matcher, or
# the gateway hands them to the SPA instead of this router.

_AUTH_PAGE_STYLE = """
  body{margin:0;background:#08171c;color:#e8f4f2;font-family:Arial,Helvetica,
       sans-serif;display:flex;min-height:100vh;align-items:center;
       justify-content:center}
  .card{background:#0e242b;border:1px solid #1d3a42;border-radius:14px;
        padding:36px 32px;max-width:420px;width:90%;text-align:center}
  h1{color:#7fe3c8;font-size:22px;margin:0 0 14px}
  p{color:#cfe6e0;line-height:1.6}
  input{width:100%;box-sizing:border-box;padding:12px;margin:8px 0;
        border-radius:8px;border:1px solid #2a4a52;background:#122b33;
        color:#e8f4f2;font-size:15px}
  button{background:#18b98a;color:#04211a;border:0;border-radius:8px;
         padding:12px 26px;font-weight:bold;font-size:15px;cursor:pointer;
         margin-top:10px}
  .err{color:#ff9c9c}.okc{color:#7fe3c8}
"""


@router.get("/verify-email", response_class=HTMLResponse, include_in_schema=False)
async def verify_email_page() -> HTMLResponse:
    """Landing page for the email-verification link: POSTs the token, shows ✓."""
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FarryOn — verify email</title><style>{_AUTH_PAGE_STYLE}</style></head>
<body><div class="card"><h1>Verifying your email…</h1><p id="msg">One moment.</p>
<script>
(async () => {{
  const t = new URLSearchParams(location.search).get('token');
  const msg = document.getElementById('msg');
  if (!t) {{ msg.textContent = 'This link is missing its token.'; return; }}
  try {{
    const r = await fetch('/api/v1/auth/verify-email', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{token: t}})
    }});
    if (r.ok) {{
      document.querySelector('h1').textContent = 'Email verified \\u2713';
      msg.className = 'okc';
      msg.textContent = 'Your FarryOn account is active — you can go back to the app and sign in.';
    }} else {{
      const d = await r.json().catch(() => ({{}}));
      document.querySelector('h1').textContent = 'Link not valid';
      msg.className = 'err';
      msg.textContent = (d.error && d.error.message) || d.detail ||
        'This verification link is invalid or has expired. Request a new one from the app.';
    }}
  }} catch (e) {{
    msg.className = 'err'; msg.textContent = 'Network error — try again.';
  }}
}})();
</script></div></body></html>"""
    return HTMLResponse(html, headers={"Content-Security-Policy": _SITE_CSP})


@router.get(
    "/reset-password", response_class=HTMLResponse, include_in_schema=False
)
async def reset_password_page() -> HTMLResponse:
    """Landing page for reset/invite links: asks for the new password twice."""
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FarryOn — set password</title><style>{_AUTH_PAGE_STYLE}</style></head>
<body><div class="card"><h1 id="title">Choose a new password</h1>
<p id="msg">At least 8 characters.</p>
<form id="f">
  <input type="password" id="p1" placeholder="New password" minlength="8" required>
  <input type="password" id="p2" placeholder="Repeat password" minlength="8" required>
  <button type="submit">Save password</button>
</form>
<script>
const q = new URLSearchParams(location.search);
if (q.get('invite')) document.getElementById('title').textContent =
  'Activate your FarryOn account';
document.getElementById('f').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const msg = document.getElementById('msg');
  const p1 = document.getElementById('p1').value;
  const p2 = document.getElementById('p2').value;
  if (p1 !== p2) {{ msg.className='err'; msg.textContent='Passwords do not match.'; return; }}
  const t = q.get('token');
  if (!t) {{ msg.className='err'; msg.textContent='This link is missing its token.'; return; }}
  try {{
    const r = await fetch('/api/v1/auth/reset-password', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{token: t, new_password: p1}})
    }});
    if (r.ok) {{
      document.getElementById('f').style.display = 'none';
      document.getElementById('title').textContent = 'Password saved \\u2713';
      msg.className = 'okc';
      msg.textContent = 'You can now sign in to FarryOn with your new password.';
    }} else {{
      const d = await r.json().catch(() => ({{}}));
      msg.className = 'err';
      msg.textContent = (d.error && d.error.message) || d.detail ||
        'This link is invalid or has expired. Request a new one from the app.';
    }}
  }} catch (e) {{ msg.className='err'; msg.textContent='Network error — try again.'; }}
}});
</script></div></body></html>"""
    return HTMLResponse(html, headers={"Content-Security-Policy": _SITE_CSP})


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


# ---- legal pages ----------------------------------------------------------
# Stripe requires a live site to publish terms, a refund policy and a privacy
# notice before it will activate an account for real payments, and a person
# about to pay is entitled to read them anyway. Kept here (not in the SPA)
# for the same reason as the auth pages: they belong to the public site.
# NOTE: both paths must also be in admin/Caddyfile's @backend matcher.

_LEGAL_STYLE = """
  body{margin:0;background:#08171c;color:#cfe6e0;font-family:Arial,Helvetica,
       sans-serif;line-height:1.65}
  .wrap{max-width:760px;margin:0 auto;padding:44px 22px 72px}
  h1{color:#7fe3c8;font-size:26px;margin:0 0 6px}
  h2{color:#7fe3c8;font-size:17px;margin:30px 0 8px}
  .upd{color:#7fa39b;font-size:13px;margin:0 0 26px}
  a{color:#18b98a}
  li{margin:5px 0}
  .foot{margin-top:40px;border-top:1px solid #1d3a42;padding-top:16px;
        font-size:13px;color:#7fa39b}
"""

_CONTACT_EMAIL = "students@izylrn.com"


def _legal_page(title: str, updated: str, body: str) -> HTMLResponse:
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FarryOn — {title}</title><style>{_LEGAL_STYLE}</style></head>
<body><div class="wrap"><h1>{title}</h1><p class="upd">Last updated {updated}</p>
{body}
<p class="foot">FarryOn · <a href="/">farryon.izylrn.com</a> ·
Questions: <a href="mailto:{_CONTACT_EMAIL}">{_CONTACT_EMAIL}</a></p>
</div></body></html>"""
    return HTMLResponse(html, headers={"Content-Security-Policy": _SITE_CSP})


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms_page() -> HTMLResponse:
    return _legal_page(
        "Terms &amp; Refund Policy",
        "5 September 2026",
        """
<h2>What FarryOn is</h2>
<p>FarryOn is a voice assistant for your phone and (optionally) smart glasses.
It listens when you start a session, answers, and can act on your behalf —
saving notes, setting reminders, reading and sending email, opening WhatsApp
or Telegram messages for you to send, and describing what the camera sees.</p>

<h2>Your account</h2>
<ul>
<li>You need an account, and the email address on it must be verified.</li>
<li>Keep your password to yourself; you are responsible for what happens under
your account.</li>
<li>Don't use FarryOn to break the law, to harass anyone, or to record people
who have not agreed to be recorded. Local recording laws are yours to follow.</li>
<li>We may suspend an account that is being used this way.</li>
</ul>

<h2>Plans and billing</h2>
<ul>
<li>The free tier is a trial allowance, not an unlimited plan.</li>
<li>Paid plans are monthly subscriptions, charged in advance through Stripe.
We never see or store your card details.</li>
<li>A subscription renews automatically each month until you cancel.</li>
<li>Prices are in US dollars and may change; a change never applies to a month
you have already paid for.</li>
</ul>

<h2>Cancellation and refunds</h2>
<ul>
<li>You can cancel at any time. Your plan stays active until the end of the
period you have already paid for, and does not renew after that.</li>
<li><strong>Unhappy in the first 14 days of your first paid month? Write to us
and we will refund it in full.</strong></li>
<li>If the service is substantially unavailable for a prolonged period during a
month you paid for, ask us and we will refund that month.</li>
<li>Beyond that, part-months are not refunded — cancelling simply stops the
next charge.</li>
</ul>

<h2>What we don't promise</h2>
<p>FarryOn uses AI models that can be wrong. Check anything that matters before
acting on it. The service is provided as is, without warranty, and we are not
liable for indirect losses. Nothing here limits rights you have by law.</p>

<h2>Changes</h2>
<p>If these terms change materially, we will say so on this page and, for
paying users, by email before the change takes effect.</p>
""",
    )


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_page() -> HTMLResponse:
    return _legal_page(
        "Privacy Policy",
        "5 September 2026",
        """
<h2>The short version</h2>
<p>FarryOn holds what it needs to answer you and to run your account. It does
not sell your data, and it does not listen when a session is not running.</p>

<h2>What we hold</h2>
<ul>
<li><strong>Account:</strong> your email address, display name, and a hashed
password (never the password itself).</li>
<li><strong>Your content:</strong> notes, reminders, saved contacts and chat
transcripts you create, so you can read them back.</li>
<li><strong>Usage:</strong> how many voice minutes, image scans and searches
you use, to enforce plan limits and to bill correctly.</li>
<li><strong>Diagnostics:</strong> timing and error logs. These do not contain
your conversations.</li>
</ul>

<h2>Voice, camera and location</h2>
<ul>
<li>The microphone is streamed only while a session is running, and only to the
AI provider that answers you. It is not stored as audio on our servers.</li>
<li>The camera is used when you ask about something in front of you. The photo
is used to answer that question; photos taken on the glasses stay on the
glasses until you sync them to your own phone.</li>
<li>Location is used only when you ask for something that needs it, and it is
not kept as a history.</li>
</ul>

<h2>Email, WhatsApp and Telegram</h2>
<p>If you connect a mailbox, its app password is stored encrypted on your phone
and used only to fetch or send the mail you ask for. WhatsApp and SMS are
opened on your device with the message ready — you press send.</p>

<h2>Who else sees it</h2>
<ul>
<li><strong>AI providers</strong> (Google Gemini, and OpenAI if you pick it)
receive what is needed to answer your request.</li>
<li><strong>Stripe</strong> handles payments and holds your card details; we
receive only the subscription status.</li>
<li>Nobody else. We do not sell or rent your data or use it for advertising.</li>
</ul>

<h2>Keeping and deleting</h2>
<p>Your content stays until you delete it or close your account. Ask us and we
will delete your account and its data. Diagnostic logs roll over on their own.</p>

<h2>Children</h2>
<p>FarryOn is not intended for children under 13.</p>
""",
    )
