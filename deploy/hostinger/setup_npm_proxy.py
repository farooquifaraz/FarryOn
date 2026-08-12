#!/usr/bin/env python3
"""Point Nginx Proxy Manager at FarryOn — proxy host, WebSockets, TLS.

Run this once on the VPS, after a successful deploy, when FarryOn runs in
``behind-proxy`` mode and Nginx Proxy Manager owns ports 80/443:

    python3 deploy/hostinger/setup_npm_proxy.py --email you@example.com

It reads DOMAIN and FARRYON_HTTP_PORT from the repo's .env, then drives NPM's
own API to do exactly what the admin UI would:

1. log in and get a token
2. create (or update, if one already exists for the domain) a proxy host
   forwarding the domain to FarryOn's gateway
3. enable **WebSocket upgrades** — without this /ws/live never connects, and
   the app fails in a way that looks like a server problem
4. set generous proxy read/send timeouts — NPM defaults to 60s, which cuts
   live voice sessions off mid-sentence
5. request a Let's Encrypt certificate and force SSL
6. verify https://<domain>/healthz actually answers

Safe to re-run: it updates the existing host rather than creating duplicates,
and keeps a certificate that is already attached.

The NPM password is read from the NPM_PASSWORD environment variable, or
prompted for without echo. It is never passed on the command line (where it
would land in shell history and the process list).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUTS = "proxy_read_timeout 3600s;\nproxy_send_timeout 3600s;"


class NpmError(RuntimeError):
    pass


def api(
    base: str,
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: dict | None = None,
) -> object:
    """Call the NPM API and return the decoded JSON body."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise NpmError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise NpmError(f"{method} {path} -> cannot reach NPM at {base}: {exc.reason}") from exc


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            # .env is often authored on Windows; strip the trailing CR.
            out[key.strip()] = value.strip().replace("\r", "")
    return out


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    env = read_env(repo_root / ".env")

    ap = argparse.ArgumentParser(description="Configure Nginx Proxy Manager for FarryOn")
    ap.add_argument("--email", required=True, help="your Nginx Proxy Manager login email")
    ap.add_argument("--npm", default="http://127.0.0.1:81", help="NPM admin URL")
    ap.add_argument("--domain", default=env.get("DOMAIN", ""), help="defaults to DOMAIN in .env")
    ap.add_argument("--forward-host", default="172.17.0.1", help="docker0 gateway")
    ap.add_argument(
        "--forward-port",
        default=env.get("FARRYON_HTTP_PORT", "8080"),
        help="defaults to FARRYON_HTTP_PORT in .env",
    )
    ap.add_argument(
        "--le-email",
        default=env.get("ACME_EMAIL", ""),
        help="Let's Encrypt contact (defaults to ACME_EMAIL in .env, else --email)",
    )
    ap.add_argument("--no-ssl", action="store_true", help="skip the certificate step")
    args = ap.parse_args()

    if not args.domain:
        print("ERROR: no domain. Pass --domain or set DOMAIN in .env.", file=sys.stderr)
        return 1
    le_email = args.le_email or args.email

    password = os.environ.get("NPM_PASSWORD") or getpass.getpass(
        f"Nginx Proxy Manager password for {args.email}: "
    )
    if not password:
        print("ERROR: no password given.", file=sys.stderr)
        return 1

    base = args.npm.rstrip("/")

    # ---- 1. authenticate ---------------------------------------------------
    print(f"==> logging in to {base}")
    tok = api(base, "/api/tokens", method="POST", body={"identity": args.email, "secret": password})
    token = tok.get("token") if isinstance(tok, dict) else None
    if not token:
        raise NpmError("login succeeded but returned no token")
    print("    ok")

    # ---- 2. find an existing host for this domain --------------------------
    hosts = api(base, "/api/nginx/proxy-hosts", token=token)
    existing = next(
        (h for h in hosts if args.domain in h.get("domain_names", [])),
        None,
    ) if isinstance(hosts, list) else None

    payload = {
        "domain_names": [args.domain],
        "forward_scheme": "http",
        "forward_host": args.forward_host,
        "forward_port": int(args.forward_port),
        # /ws/live is a WebSocket: without this the app connects, then dies.
        "allow_websocket_upgrade": True,
        "block_exploits": True,
        "caching_enabled": False,
        "http2_support": True,
        "hsts_enabled": False,
        "hsts_subdomains": False,
        # NPM's default read timeout is 60s, which would cut a live session off
        # mid-sentence. These sockets are meant to stay open for the whole call.
        "advanced_config": TIMEOUTS,
        "locations": [],
        "meta": {},
    }

    if existing:
        host_id = existing["id"]
        # Keep any certificate already attached; the SSL step below manages it.
        payload["certificate_id"] = existing.get("certificate_id") or 0
        payload["ssl_forced"] = bool(existing.get("ssl_forced"))
        print(f"==> updating existing proxy host #{host_id} for {args.domain}")
        api(base, f"/api/nginx/proxy-hosts/{host_id}", token=token, method="PUT", body=payload)
    else:
        payload["certificate_id"] = 0
        payload["ssl_forced"] = False
        print(f"==> creating proxy host {args.domain} -> http://{args.forward_host}:{args.forward_port}")
        created = api(base, "/api/nginx/proxy-hosts", token=token, method="POST", body=payload)
        host_id = created["id"]
    print(f"    websockets ON, timeouts 3600s, host id {host_id}")

    # ---- 3. certificate ----------------------------------------------------
    if args.no_ssl:
        print("==> skipping SSL (--no-ssl)")
    else:
        cert_id = (existing or {}).get("certificate_id") or 0
        if cert_id:
            print(f"==> certificate #{cert_id} already attached, keeping it")
        else:
            print(f"==> requesting a Let's Encrypt certificate for {args.domain}")
            print("    (this takes ~30s; the domain's A record must point at this VPS)")
            # NPM validates `meta` against a strict schema that differs by
            # version: older builds take the contact email and ToS agreement
            # per certificate, newer ones take neither (their SSL dialog has no
            # such fields) and reject them as additional properties. Offer the
            # richest form first and fall back, so one script serves both.
            meta_variants = [
                {"letsencrypt_email": le_email, "letsencrypt_agree": True, "dns_challenge": False},
                {"dns_challenge": False},
                {},
            ]
            cert = None
            for meta in meta_variants:
                try:
                    cert = api(
                        base,
                        "/api/nginx/certificates",
                        token=token,
                        method="POST",
                        body={
                            "domain_names": [args.domain],
                            "provider": "letsencrypt",
                            "meta": meta,
                        },
                    )
                    break
                except NpmError as exc:
                    # Only a schema rejection is worth retrying with less; a
                    # real failure (DNS not pointing here, rate limit) would
                    # fail identically for every variant.
                    if "additional propert" not in str(exc).lower():
                        raise
                    print("    (this NPM rejects some meta fields; retrying with fewer)")
            if cert is None:
                raise NpmError(
                    "NPM rejected every certificate payload shape. Request the "
                    "certificate from the SSL tab of the proxy host in the UI, "
                    "then re-run this script — it will keep the existing one."
                )
            cert_id = cert["id"]
            print(f"    issued, certificate id {cert_id}")

        payload["certificate_id"] = cert_id
        payload["ssl_forced"] = True
        api(base, f"/api/nginx/proxy-hosts/{host_id}", token=token, method="PUT", body=payload)
        print("    attached to the proxy host, Force SSL on")

    # ---- 4. verify ---------------------------------------------------------
    scheme = "http" if args.no_ssl else "https"
    url = f"{scheme}://{args.domain}/healthz"
    print(f"==> verifying {url}")
    ctx = ssl.create_default_context()
    for attempt in range(10):
        try:
            with urllib.request.urlopen(url, timeout=10, context=ctx) as resp:
                if resp.status == 200:
                    print(f"    OK — FarryOn is live at {scheme}://{args.domain}")
                    return 0
        except Exception as exc:  # noqa: BLE001 — any failure here is just "not yet"
            last = exc
        time.sleep(6)

    print(f"    not answering yet ({last}).", file=sys.stderr)
    print("    The proxy host is configured; give DNS/TLS a minute and retry:", file=sys.stderr)
    print(f"      curl -v {url}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NpmError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
