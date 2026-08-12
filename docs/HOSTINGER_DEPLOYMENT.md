# FarryOn — Deploying the Complete App on Hostinger

> End-to-end runbook for shipping FarryOn to a **Hostinger VPS**: backend
> (FastAPI + WebSockets), Postgres, the admin panel, automatic HTTPS, and the
> mobile app pointed at your new server.
>
> Platform-agnostic background (scaling, WebSockets, rollouts):
> [`DEPLOYMENT.md`](./DEPLOYMENT.md).

---

## 0. Which Hostinger product?

| Hostinger product | Works for FarryOn? | Why |
| ----------------- | ------------------ | --- |
| Shared / Premium / Business web hosting | ❌ | PHP + static files only. No Python processes, no long-lived WebSockets, no Docker. |
| Cloud hosting | ❌ | Same runtime restrictions as shared. |
| **VPS (KVM plans)** | ✅ | Full root access + Docker. This is what this guide uses. |

**Recommended plan:** KVM 2 (2 vCPU / 8 GB RAM / 100 GB NVMe) — comfortable
for the whole stack. KVM 1 (4 GB) works for a demo/small load. The stack is
CPU-bound on concurrent live sessions, so scale the plan with usage
([`DEPLOYMENT.md`](./DEPLOYMENT.md) §4.4).

**What ends up running** (one `docker compose` stack, defined in
[`docker-compose.prod.yml`](../docker-compose.prod.yml)):

```
Internet ──► Caddy (:80/:443, auto-HTTPS via Let's Encrypt)
              ├── /                          → admin panel (static SPA)
              ├── /api/*  /healthz  /readyz  → backend:8000
              ├── /detect                    → backend:8000
              └── /ws/live (WebSocket)       → backend:8000
             backend ──► Postgres 16 (internal network only)
             [--profile obs] Prometheus + Grafana, localhost-only
```

The mobile app is **not** hosted here — it's built as an APK/IPA and configured
to talk to `https://<your-domain>` (§7).

---

## 1. Create the VPS

1. In **hPanel → VPS**, buy/pick a KVM plan.
2. Choose the closest region **to your AI provider**, not to you — the
   dominant latency is backend ↔ Gemini/OpenAI. For Gemini/OpenAI, a US or EU
   datacenter is a good default.
3. For the OS template pick **Ubuntu 24.04 with Docker** (under
   *OS with Control Panel / Applications*). If you chose plain Ubuntu,
   install Docker later with one command (§3).
4. Set the root password / add your SSH key when prompted.
5. Note the VPS **IP address** from the VPS dashboard.

## 2. Point your domain at the VPS

You need one subdomain (e.g. `app.farryon.example`) with an **A record** to the
VPS IP:

- Domain on Hostinger: **hPanel → Domains → your domain → DNS / Name Servers →
  Add record**: type `A`, name `app`, content `<VPS IP>`, TTL default.
- Domain elsewhere: same A record at your registrar.

Verify before continuing (Caddy can only obtain a TLS certificate once DNS
resolves):

```bash
dig +short app.farryon.example    # must print the VPS IP
```

## 3. SSH in and prepare the host

```bash
ssh root@<VPS-IP>

# If you did NOT pick the Docker template:
curl -fsSL https://get.docker.com | sh

docker --version && docker compose version   # sanity check

# Basic firewall: SSH + HTTP(S) only.
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
```

> Hostinger also has a panel-level firewall (**VPS → Settings → Firewall**).
> If you enable it, open the same ports there: 22, 80, 443 (TCP) and
> optionally 443/UDP for HTTP/3.

## 4. Get the code onto the VPS

```bash
cd /opt
git clone https://github.com/farooquifaraz/FarryOn.git farryon
cd farryon
```

For a private repo, create a fine-grained GitHub **personal access token**
(read-only, this repo) and use
`git clone https://<TOKEN>@github.com/farooquifaraz/FarryOn.git farryon`,
or add the VPS's SSH key as a read-only deploy key on the repo.

## 5. Configure production environment

```bash
cp deploy/hostinger/env.production.example .env
nano .env
```

Minimum you must fill in:

| Variable | Value |
| -------- | ----- |
| `DOMAIN` | `app.farryon.example` (the A record from §2) |
| `ACME_EMAIL` | your email (Let's Encrypt expiry notices) |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` |
| `JWT_SECRET` | `openssl rand -hex 32` — any non-default value **turns WS auth on** |
| `ALLOWED_ORIGINS` | `https://app.farryon.example` |
| `FIRST_SUPER_ADMIN_EMAIL` / `_PASSWORD` | your first admin-panel login |
| `AI_PROVIDER` + its API key | `gemini` + `GEMINI_API_KEY` (or `openai`/`grok`; `mock` for a free offline smoke test) |

Everything else has safe defaults; the full reference is
[`backend/.env.example`](../backend/.env.example) and
[`DEPLOYMENT.md`](./DEPLOYMENT.md) §5.

## 5b. Making the cloud behave like your local machine

Every setting in the template carries the **same default the backend uses
locally**, so anything you leave unset behaves identically in both places.
Parity breaks in one direction only: values you set in your **local
`backend/.env`** and don't carry over here.

That gap is quiet rather than loud. A missing `GEMINI_API_KEY` fails visibly,
but a missing `TELEGRAM_BOT_TOKEN`, `VISION_API_KEY`, `WEB_SEARCH_API_KEY`, or
`STRIPE_SECRET_KEY` doesn't error — the backend just treats that feature as
not configured and disables it. The app looks fine; one capability is simply
gone.

**The fast path** — on the machine where FarryOn already works, let the
generator do the merge:

```bash
python deploy/hostinger/make_prod_env.py --domain app.farryon.example
```

It reads `backend/.env`, carries over everything you have set, generates a
fresh `POSTGRES_PASSWORD` / `JWT_SECRET`, substitutes the production-only
values, and writes `farryon-env.txt` (gitignored) — paste that file into the
`FARRYON_ENV` secret. It also prints which feature keys are set or missing,
and warns if `AI_PROVIDER=mock` would ship canned answers to production.
Secrets go to the file, never to the terminal.

To do it by hand instead, open your local `backend/.env` next to the
production file and copy across **every non-empty value**, paying particular
attention to:

| Area | Variables |
| ---- | --------- |
| AI provider | `GEMINI_API_KEY` / `OPENAI_API_KEY` / `GROK_API_KEY`, the matching `*_MODEL`, `ALLOWED_PROVIDERS` |
| Live translation | `TRANSLATE_PROVIDER`, `GEMINI_TRANSLATE_MODEL`, `TRANSLATE_ALLOWED_TARGET_LANGS` |
| Vision / Finder | `VISION_API_KEY`, `VISION_FRAME_MODE`, `VISION_ON_DEMAND_ONLY` |
| Messaging | `TELEGRAM_*`, `WHATSAPP_*`, `DEFAULT_COUNTRY_CODE` |
| Web search | `WEB_SEARCH_PROVIDER` + `WEB_SEARCH_API_KEY` (+ the fallback pair) |
| SSO | `GOOGLE_CLIENT_ID` / `_SECRET`, `MICROSOFT_*` |
| Billing | `STRIPE_*`, `BILLING_WEBHOOK_SECRET` |
| Cost & quotas | `MAX_SESSION_SECONDS`, `CONTEXT_*`, `QUOTA_ENFORCEMENT_ENABLED`, `DEFAULT_PLAN` |

Four values must **not** be copied from local — production needs its own:

- `DATABASE_URL` — local SQLite → the stack's Postgres (leave unset; it is
  derived from `POSTGRES_PASSWORD`).
- `JWT_SECRET` — the local default keeps WS auth **off**; production needs a
  real secret, which is what turns auth on.
- `ALLOWED_ORIGINS` — `*` locally, your real domain in production.
- `SSO_REDIRECT_BASE_URL` — `http://localhost:8000` → `https://$DOMAIN`.

To confirm what the running server actually loaded, check a feature's
readiness rather than guessing:

```bash
curl https://app.farryon.example/readyz          # DB + gateway configured
docker compose -f docker-compose.prod.yml exec backend \
  python -c "from app.config import get_settings as s; c=s(); \
print('ai:', c.ai_provider, '| gemini key:', bool(c.gemini_api_key), \
'| vision:', bool(c.vision_api_key), '| search:', c.web_search_provider)"
```

## 6. Deploy

```bash
./deploy/hostinger/deploy.sh
```

The script validates `.env`, builds the images, runs Alembic migrations
(`migrate` one-shot container), starts backend + Postgres + Caddy, seeds the
super-admin, and waits until `https://$DOMAIN/healthz` answers. First run
takes a few minutes (image builds + TLS certificate issuance).

**Verify:**

```bash
curl https://app.farryon.example/healthz     # {"status":"ok"} — liveness
curl https://app.farryon.example/readyz      # readiness incl. DB ping
```

Open `https://app.farryon.example` in a browser → the **admin panel** loads;
log in with `FIRST_SUPER_ADMIN_EMAIL` / `FIRST_SUPER_ADMIN_PASSWORD`.

## 7. Point the mobile app at your server

The Flutter app connects to the backend over `wss://`:

1. Build the APK: GitHub Actions →
   [`build-apk.yml`](../.github/workflows/build-apk.yml) (or locally:
   `cd mobile && flutter build apk --release`).
2. In the app's **Settings → Server**, set the server address to
   `https://app.farryon.example` (the app derives `wss://…/ws/live` from it).
3. Because `JWT_SECRET` is set, `/ws/live` requires a valid token — sign in
   through the app so it obtains one via `/api/v1/auth/*`.

For Google Sign-In, complete
[`GOOGLE_SIGN_IN_SETUP.md`](./GOOGLE_SIGN_IN_SETUP.md) and set
`SSO_REDIRECT_BASE_URL=https://app.farryon.example` in `.env`, then redeploy.

## 7b. Shared VPS: running behind an existing proxy (Nginx Proxy Manager)

If the VPS already runs another app whose proxy owns ports 80/443 (e.g.
**Nginx Proxy Manager**), FarryOn can't bind them. Run it in `behind-proxy`
mode instead — the existing app is untouched:

1. In `.env`, set:

   ```bash
   PROXY_MODE=behind-proxy
   FARRYON_HTTP_PORT=8080        # any free port
   ```

   FarryOn's gateway then serves plain HTTP on `172.17.0.1:8080` — the
   docker0 gateway address, reachable by the host and by other containers
   (like the proxy), but **not** from the public internet.

2. Deploy as usual (`./deploy/hostinger/deploy.sh` or push to main).

3. In **Nginx Proxy Manager** (usually `http://<VPS-IP>:81`):
   - **Hosts → Proxy Hosts → Add Proxy Host**
   - *Domain Names*: `app.farryon.example`
   - *Scheme*: `http` · *Forward Hostname/IP*: `172.17.0.1` · *Forward
     Port*: `8080`
   - Enable **Websockets Support** (required — `/ws/live` is a WebSocket)
     and *Block Common Exploits*
   - **SSL tab**: *Request a new SSL Certificate* (Let's Encrypt), enable
     *Force SSL* and *HTTP/2 Support* → **Save**

4. Long-lived sockets: NPM's default `proxy_read_timeout` (60s) can drop
   idle live sessions. In the Proxy Host's **Advanced** tab add:

   ```nginx
   proxy_read_timeout 3600s;
   proxy_send_timeout 3600s;
   ```

`https://app.farryon.example` now serves the admin panel and `wss://…/ws/live`
reaches the backend, with TLS handled by NPM.

## 8. Updating (every future release)

```bash
cd /opt/farryon
./deploy/hostinger/deploy.sh      # git pull + rebuild + migrate + restart
```

Rollouts drain WebSockets rather than migrate them; live clients reconnect
with `resumeId` and re-attach ([`DEPLOYMENT.md`](./DEPLOYMENT.md) §12). A brief
per-session blip during deploy is expected and handled by the protocol.

Optional: automate this from GitHub with the manual workflow in
[`.github/workflows/deploy-hostinger.yml`](../.github/workflows/deploy-hostinger.yml)
after adding `HOSTINGER_SSH_HOST`, `HOSTINGER_SSH_USER`, and
`HOSTINGER_SSH_KEY` repository secrets (Settings → Secrets and variables →
Actions).

## 9. Observability (optional)

```bash
# Set GRAFANA_PASSWORD in .env first.
docker compose -f docker-compose.prod.yml --profile obs up -d
```

Prometheus (`:9090`) and Grafana (`:3000`) bind to **localhost only** — reach
them through an SSH tunnel instead of exposing them publicly:

```bash
ssh -L 3000:localhost:3000 root@<VPS-IP>   # then open http://localhost:3000
```

## 10. Backups

- **Database** (the only stateful thing that matters):

  ```bash
  # /etc/cron.d/farryon-backup — daily 03:15, keep 14 days
  15 3 * * * root docker compose -f /opt/farryon/docker-compose.prod.yml exec -T postgres \
    pg_dump -U farryon farryon | gzip > /opt/farryon-backups/farryon-$(date +\%F).sql.gz \
    && find /opt/farryon-backups -mtime +14 -delete
  ```

  (Create `/opt/farryon-backups` first; copy off-host if the data is precious.)
- **VPS snapshots:** hPanel → VPS → Snapshots & Backups (Hostinger keeps a
  weekly backup; take a manual snapshot before risky changes).
- **TLS certs** live in the `caddy_data` volume and are re-issued automatically
  if lost — nothing to back up there.

## 11. Troubleshooting

| Symptom | Check / fix |
| ------- | ----------- |
| `deploy.sh` fails env validation | Fill `DOMAIN`, `POSTGRES_PASSWORD`, `JWT_SECRET` in `.env`. |
| No TLS / cert errors | `dig +short $DOMAIN` must return the VPS IP; ports 80+443 open in **both** ufw and the hPanel firewall; `docker compose -f docker-compose.prod.yml logs caddy`. |
| `/healthz` OK but `/readyz` fails | DB problem — `docker compose -f docker-compose.prod.yml logs backend postgres`; check `POSTGRES_PASSWORD`/`DATABASE_URL`. |
| `migrate` container fails | `docker compose -f docker-compose.prod.yml logs migrate`; migrations run against the compose Postgres, which must be healthy first. |
| App connects then drops / `unauthorized` | The app must be signed in (JWT auth is on in prod); confirm the phone's server address uses `https://` (not `http://`). |
| Voice sessions fail with provider errors | Wrong/missing `GEMINI_API_KEY` (or provider key); try `AI_PROVIDER=mock` to isolate infra from provider issues. |
| Admin panel loads but API calls 401/CORS | `ALLOWED_ORIGINS` must include `https://$DOMAIN` exactly. |
| Disk filling up | `docker system prune -af --volumes=false` clears old images (never prune volumes — `pgdata` lives there). |

More symptom → action pairs: [`DEPLOYMENT.md`](./DEPLOYMENT.md) §12.2.
