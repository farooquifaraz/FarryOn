#!/usr/bin/env python3
"""Build the production env (the ``FARRYON_ENV`` secret) from your local one.

Run this on the machine where FarryOn already works. It reads
``backend/.env``, carries every value you have set over to production, and
replaces the handful that must differ in the cloud — so the deployed app
behaves like your local one instead of quietly missing features whose keys
never made it across.

    python deploy/hostinger/make_prod_env.py --domain farryon.example.com

It writes ``farryon-env.txt`` next to the repo (gitignored, owner-readable).
Open that file, copy all of it, and paste it into the ``FARRYON_ENV``
repository secret on GitHub. Secrets are never printed to the terminal — the
summary lists key names and whether they are set, never values.

Not copied from local, because production needs its own (see
docs/HOSTINGER_DEPLOYMENT.md §5b):

* ``DATABASE_URL``      local SQLite -> the stack's Postgres (derived)
* ``JWT_SECRET``        the local default leaves WS auth OFF; generated here
* ``POSTGRES_PASSWORD`` generated here
* ``ALLOWED_ORIGINS``   ``*`` locally -> your real domain
* ``SSO_REDIRECT_BASE_URL`` localhost -> https://<domain>
* ``HOST`` / ``PORT``   set by the compose stack
"""

from __future__ import annotations

import argparse
import re
import secrets
import string
import sys
from pathlib import Path

# Values that must NOT be carried over from the local .env: production either
# derives them, generates them, or needs a different value entirely.
NEVER_COPY = {
    "DATABASE_URL",
    "JWT_SECRET",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "ALLOWED_ORIGINS",
    "SSO_REDIRECT_BASE_URL",
    "HOST",
    "PORT",
    # Deployment-shape settings, supplied by the flags below.
    "DOMAIN",
    "ACME_EMAIL",
    "PROXY_MODE",
    "FARRYON_HTTP_PORT",
    "UVICORN_WORKERS",
}

# Feature keys worth calling out when they are missing: each one disables a
# capability silently rather than erroring.
NOTABLE = {
    "GEMINI_API_KEY": "Gemini realtime voice/vision",
    "VISION_API_KEY": "landmark & product Finder",
    "TRANSLATE_PROVIDER": "live translation (unset follows AI_PROVIDER)",
    "WEB_SEARCH_API_KEY": "real web search (mock without it)",
    "TELEGRAM_BOT_TOKEN": "Telegram messaging",
    "WHATSAPP_TOKEN": "WhatsApp messaging",
    "GOOGLE_CLIENT_ID": "Google Sign-In",
    "STRIPE_SECRET_KEY": "Stripe billing",
}


# Hosts that only resolve on the developer's own machine or LAN. A value
# pointing at one of these works locally and silently breaks in production —
# a Stripe redirect to 192.168.x.x sends the paying user nowhere.
LOCAL_HOSTS = re.compile(
    r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|"
    r"192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)"
    r"(:\d+)?",
    re.I,
)


def gen_secret(length: int = 32) -> str:
    """Alphanumeric only — the value lands in a URL and in YAML."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def read_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines, preserving values verbatim."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]

    ap = argparse.ArgumentParser(
        description="Build the production FARRYON_ENV from your local backend/.env"
    )
    ap.add_argument("--domain", required=True, help="e.g. farryon.example.com")
    ap.add_argument("--email", help="ACME/Let's Encrypt contact (defaults to the admin email)")
    ap.add_argument("--admin-email", help="first super-admin login")
    ap.add_argument(
        "--proxy-mode",
        choices=["standalone", "behind-proxy"],
        default="behind-proxy",
        help="behind-proxy (default) when another app already owns ports 80/443",
    )
    ap.add_argument("--http-port", default="8080", help="behind-proxy listen port")
    ap.add_argument("--workers", default="2", help="uvicorn workers (~vCPU count)")
    ap.add_argument(
        "--local-env",
        type=Path,
        default=repo_root / "backend" / ".env",
        help="path to your working local env file",
    )
    ap.add_argument("--out", type=Path, default=repo_root / "farryon-env.txt")
    args = ap.parse_args()

    if not args.local_env.is_file():
        print(f"ERROR: no local env at {args.local_env}", file=sys.stderr)
        print("Pass --local-env with the path to the .env your local run uses.", file=sys.stderr)
        return 1

    local = read_env(args.local_env)
    carried = {k: v for k, v in local.items() if k not in NEVER_COPY and v != ""}

    admin_email = args.admin_email or local.get("FIRST_SUPER_ADMIN_EMAIL", "")
    acme_email = args.email or admin_email
    if not acme_email:
        print("ERROR: pass --email (Let's Encrypt contact) or --admin-email.", file=sys.stderr)
        return 1

    admin_password = local.get("FIRST_SUPER_ADMIN_PASSWORD", "") or gen_secret(20)
    carried.pop("FIRST_SUPER_ADMIN_EMAIL", None)
    carried.pop("FIRST_SUPER_ADMIN_PASSWORD", None)

    # Rewrite localhost/LAN URLs to the public domain. These are callback and
    # redirect targets handed to a browser or an external service, so a
    # private address is not merely wrong here — it is unreachable.
    rewritten: list[str] = []
    for key, value in list(carried.items()):
        if LOCAL_HOSTS.search(value):
            carried[key] = LOCAL_HOSTS.sub(f"https://{args.domain}", value)
            rewritten.append(key)

    lines = [
        "# FarryOn production env — generated by deploy/hostinger/make_prod_env.py",
        "# Paste the whole file into the FARRYON_ENV repository secret.",
        "",
        "# ---- deployment shape ----",
        f"DOMAIN={args.domain}",
        f"ACME_EMAIL={acme_email}",
        f"PROXY_MODE={args.proxy_mode}",
        f"FARRYON_HTTP_PORT={args.http_port}",
        f"UVICORN_WORKERS={args.workers}",
        "",
        "# ---- generated for production (not your local values) ----",
        f"POSTGRES_PASSWORD={gen_secret(32)}",
        f"JWT_SECRET={gen_secret(48)}",
        f"ALLOWED_ORIGINS=https://{args.domain}",
        f"SSO_REDIRECT_BASE_URL=https://{args.domain}",
        "",
        "# ---- first admin login ----",
        f"FIRST_SUPER_ADMIN_EMAIL={admin_email}",
        f"FIRST_SUPER_ADMIN_PASSWORD={admin_password}",
        "",
        f"# ---- carried over from {args.local_env.name} ----",
    ]
    lines += [f"{k}={carried[k]}" for k in sorted(carried)]
    text = "\n".join(lines) + "\n"

    args.out.write_text(text, encoding="utf-8")
    try:
        args.out.chmod(0o600)
    except OSError:
        pass  # Windows — best effort.

    # Summary: names and set/unset only. Never values.
    print(f"Wrote {args.out}")
    print(f"  carried over {len(carried)} settings from {args.local_env}")
    for key in rewritten:
        print(f"  rewrote {key}: local/LAN address -> https://{args.domain}")
    if local.get("FIRST_SUPER_ADMIN_PASSWORD", "") == "":
        print("  admin password: GENERATED — read it from the file, you need it to log in")
    print()
    print("Feature keys:")
    for key, what in NOTABLE.items():
        state = "set" if carried.get(key) else "-- not set"
        print(f"  {key:22s} {state:11s} {what}")

    provider = carried.get("AI_PROVIDER", "mock")
    if provider == "mock":
        print()
        print("WARNING: AI_PROVIDER=mock — the cloud app would answer with canned")
        print("         responses, not a real model. Set AI_PROVIDER=gemini (or")
        print("         openai/grok) and its API key before deploying for real.")

    print()
    print("Next: open the file, copy everything, paste into the FARRYON_ENV secret at")
    print("      Settings -> Secrets and variables -> Actions -> FARRYON_ENV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
