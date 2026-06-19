# FarryOn — Deployment Plan

> How to run FarryOn locally and ship it to production: container build,
> env/secrets, Postgres, **scaling WebSockets** (workers, sticky sessions, Redis),
> **TLS/wss termination**, cloud options, observability, health checks, cost &
> latency, and a rollout runbook.
>
> Architecture context: [`ARCHITECTURE.md`](./ARCHITECTURE.md). Wire contract:
> [`PROTOCOL.md`](../PROTOCOL.md).

---

## 1. Topology

```mermaid
flowchart LR
  subgraph Edge["Edge / TLS"]
    LB["L7 LB / reverse proxy\n(nginx · Caddy · Traefik)\nwss:// termination + sticky"]
  end

  subgraph App["App tier (stateless workers)"]
    B1["uvicorn worker 1\n/ws/live + /metrics"]
    B2["uvicorn worker 2"]
    Bn["uvicorn worker N"]
  end

  subgraph State["Shared state"]
    PG[("Postgres 16")]
    RD[("Redis\nsession + pub/sub\n(at scale)")]
  end

  subgraph Obs["Observability"]
    PR["Prometheus"]
    GR["Grafana"]
  end

  subgraph AI["AI providers"]
    GEM["Gemini Live"]
    OAI["OpenAI Realtime"]
  end

  Client["Flutter / glasses"] -- "wss /ws/live" --> LB
  LB -- "sticky by session" --> B1 & B2 & Bn
  B1 & B2 & Bn --> PG
  B1 & B2 & Bn -. "at scale" .-> RD
  B1 & B2 & Bn --> GEM & OAI
  B1 & B2 & Bn -- "/metrics" --> PR --> GR
```

The app tier is **horizontally scalable and per-process stateless** *except* for
the live WebSocket itself, which is inherently sticky to the worker that holds it
(§4). Durable state lives in Postgres; cross-worker coordination (when needed)
lives in Redis.

---

## 2. Local development (docker-compose)

The repo ships a ready stack: backend + Postgres 16 + Prometheus + Grafana
([`docker-compose.yml`](../docker-compose.yml)).

```bash
# 1. Configure
cd backend && cp .env.example .env       # add GEMINI_API_KEY / OPENAI_API_KEY
                                         # or leave AI_PROVIDER=mock for offline

# 2. Bring up the whole stack
make up          # == docker compose up -d --build

#   backend     ws://localhost:8000/ws/live   (+ /metrics, /healthz)
#   postgres    localhost:5432  (farryon/farryon)
#   prometheus  http://localhost:9090
#   grafana     http://localhost:3000  (admin/admin)

make logs        # tail everything
make down        # stop (named volumes persist)
```

Notes:

- The compose `backend` service builds `./backend/Dockerfile` (owned by the
  backend; assumed present) and overrides `DATABASE_URL` to point at the compose
  Postgres (`postgresql+asyncpg://farryon:farryon@postgres:5432/farryon`).
- Prometheus scrapes `backend:8000/metrics` via
  [`deploy/prometheus/prometheus.yml`](../deploy/prometheus/prometheus.yml);
  Grafana auto-provisions the Prometheus datasource.
- With `AI_PROVIDER=mock` the stack runs fully offline (no API keys) — ideal for
  UI work and demos.

---

## 3. Container build & image

The backend image is built from `./backend/Dockerfile` (owned by the backend
agent). Expectations this deployment relies on:

- Base: slim Python 3.11; installs `backend/requirements.txt`.
- Runs uvicorn: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Exposes `8000` (serves `/ws/live`, `/metrics`, `/healthz`, `/readyz`).
- 12-factor: **all config via env** (`app/config.py`), no baked secrets.
- `--workers` / process model is set at deploy time (§4), not in the Dockerfile.

`.dockerignore` ([repo root](../.dockerignore)) keeps the context lean (no venvs,
no `mobile/`, no `data/`, no secrets).

Build & push (CI/registry):

```bash
docker build -t ghcr.io/farryon/backend:$(git rev-parse --short HEAD) ./backend
docker push  ghcr.io/farryon/backend:$(git rev-parse --short HEAD)
```

---

## 4. Scaling WebSockets (the crux)

`/ws/live` is **long-lived, stateful, and bidirectional**. That changes the
scaling rules vs. a stateless REST API.

### 4.1 Multiple uvicorn workers

A single Python process is limited (GIL + per-conn async tasks). Run **multiple
workers** and let the kernel spread accepts:

```bash
# In-container, per replica (tune to CPU):
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
# or gunicorn -k uvicorn.workers.UvicornWorker -w 4 app.main:app
```

Then run **multiple replicas** behind the LB. Capacity scales with
`replicas × workers`; pick worker count ≈ CPU cores. Because each connection
pins one read pump + one write pump + a gateway task (see
[`ARCHITECTURE.md`](./ARCHITECTURE.md) §5), the practical ceiling is concurrent
*active* sessions, not request rate.

### 4.2 Why WebSockets need connection affinity (sticky sessions)

A WebSocket is a **single TCP connection that lives on exactly one worker** for
its whole lifetime. There is no per-message re-routing. Implications:

- The **load balancer must keep that connection pinned** to its worker — true by
  definition once established (it's one TCP stream). The affinity concern is
  about **reconnects**: when a client drops and re-dials with `hello.resumeId`
  (`PROTOCOL.md` §7), you want it to land on a worker that can re-attach context.
- If session state were held only in worker memory, a reconnect to a *different*
  worker would lose it. Two fixes, used together:
  1. **Sticky routing** so reconnects from the same client prefer the same worker
     (cookie/`ip_hash`/consistent-hash on a session key).
  2. **Externalize durable state** to Postgres (transcripts, tool side-effects,
     session metadata) so *any* worker can rebuild context from `resumeId`. This
     is the robust path; stickiness is an optimization, not a correctness crutch.

> Rule: **never assume two messages from one logical session hit the same
> process across a reconnect** unless you both pin (sticky) *and* persist
> (Postgres/Redis). FarryOn persists, so a missed pin degrades gracefully.

### 4.3 Redis for shared session state & pub/sub at scale

Single-replica or sticky-only deployments don't need Redis. Add it when you need
**cross-worker / cross-replica coordination**:

- **Shared session registry / presence:** "is session X live, and on which
  worker?" — lets you fan control to the right place.
- **Pub/Sub for cross-process delivery:** if an out-of-band event (e.g. an async
  tool callback, a push from another service) must reach the worker holding a
  given socket, publish on a per-session channel; the owning worker is subscribed
  and forwards it to its write pump.
- **Distributed rate limits / locks:** e.g. ensure idempotent tool side-effects
  across retries that may hit different workers.
- **Ephemeral session cache:** hot context (last frame metadata, partial turn
  state) with TTL, to make `resumeId` re-attach fast without a Postgres hit.

```mermaid
flowchart LR
  subgraph R1["Replica A"]
    WA["worker (holds session S)"]
  end
  subgraph R2["Replica B"]
    WB["worker"]
  end
  EXT["External event for session S\n(async tool callback / push)"] --> RD["Redis pub/sub\nchannel: session:S"]
  WA -- "SUBSCRIBE session:S" --- RD
  WB -- "PUBLISH session:S" --> RD
  RD --> WA --> CLIENT["client socket on A"]
```

Redis is **not** on the hot media path (audio/video never transit Redis) — that
stays in-process for latency. Redis only carries control/coordination.

### 4.4 Capacity sketch

| Knob                         | Guidance                                                        |
| ---------------------------- | -------------------------------------------------------------- |
| workers/replica              | ≈ vCPU count                                                    |
| concurrent sessions/worker   | bounded by CPU for audio handling + provider socket fan-out; load-test it |
| scale signal                 | active-sessions gauge + CPU; scale out before ~70% CPU         |
| sticky key                   | session/user id (cookie or consistent hash), reconnect-friendly |
| Redis                        | add at multi-replica with cross-worker delivery needs          |

---

## 5. Environment & secrets

All config is env-driven (`backend/app/config.py`). Operator-facing knobs:

| Variable                | Default                                   | Notes                                            |
| ----------------------- | ----------------------------------------- | ------------------------------------------------ |
| `AI_PROVIDER`           | `mock`                                    | `gemini` \| `openai` \| `mock`.                  |
| `GEMINI_API_KEY`        | —                                         | required if `AI_PROVIDER=gemini`. **Secret.**    |
| `GEMINI_MODEL`          | `gemini-2.0-flash-live-001`               | Gemini Live model id.                            |
| `OPENAI_API_KEY`        | —                                         | required if `AI_PROVIDER=openai`. **Secret.**    |
| `OPENAI_REALTIME_MODEL` | `gpt-4o-realtime-preview`                 | OpenAI Realtime model id.                        |
| `DATABASE_URL`          | `sqlite+aiosqlite:///./farryon.db`        | prod: `postgresql+asyncpg://…`.                  |
| `WEB_SEARCH_PROVIDER`   | `mock`                                    | `mock` \| `tavily` \| `serpapi`.                 |
| `WEB_SEARCH_API_KEY`    | —                                         | required for non-mock search. **Secret.**        |
| `JWT_SECRET`            | `dev-insecure-change-me`                  | set to a real secret to **enable** WS auth. **Secret.** |
| `ALLOWED_ORIGINS`       | `*`                                       | CORS allow-list, comma-separated; lock down in prod. |
| `LOG_LEVEL`             | `INFO`                                    | structlog level.                                 |
| `TOOL_TIMEOUT_SECONDS`  | `20`                                      | per-tool execution timeout.                      |
| `HOST` / `PORT`         | `0.0.0.0` / `8000`                        | bind address.                                    |

**Secret handling**

- Local: `.env` (gitignored). Never commit real keys; only `.env.example`.
- Prod: inject via the platform secret store (Fly secrets, Render env groups,
  AWS Secrets Manager / SSM, GCP Secret Manager). Don't bake into the image.
- **Auth toggle:** WS JWT verification turns on automatically once `JWT_SECRET`
  is non-default (`config.py` `auth_enabled`). Always set it in prod so
  `?token=<jwt>` (`PROTOCOL.md` §1) is enforced.
- Restrict `ALLOWED_ORIGINS` to your app origins in prod.

---

## 6. Postgres

- **Version:** Postgres 16 (matches compose).
- **URL:** `DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/farryon`
  (async driver — the backend uses async SQLAlchemy).
- **Migrations:** run before/at deploy (e.g. Alembic `upgrade head`) — owned by
  `backend/`. Gate new replicas on a successful migration.
- **Sizing:** start small; the write path is light (notes/tasks/messages,
  transcripts, session rows). Watch connection count — async pools per worker ×
  replicas can add up; use a pooler (PgBouncer) if you fan out widely.
- **Backups:** managed-Postgres automated backups (or `pg_dump` cron) +
  point-in-time recovery for anything user-facing.
- **Dev parity:** SQLite is fine for local/CI; use Postgres in staging+prod to
  catch dialect differences early.

---

## 7. TLS / `wss://` termination

Browsers and stores require **`wss://`** (TLS). Terminate TLS at the edge and
proxy plain WS to the app tier. The proxy MUST forward the `Upgrade`/`Connection`
headers and use long read/idle timeouts (these are long-lived sockets).

**nginx**

```nginx
location /ws/live {
    proxy_pass http://farryon_backend;          # upstream of workers
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;      # WS handshake
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 3600s;                    # don't kill idle-ish sockets
    proxy_send_timeout 3600s;
}
# sticky: upstream farryon_backend { ip_hash; server b1:8000; server b2:8000; }
```

**Caddy** (automatic HTTPS — simplest):

```caddy
live.farryon.app {
    reverse_proxy b1:8000 b2:8000 {
        lb_policy ip_hash          # affinity for reconnects
    }
    # Caddy proxies WS upgrades transparently; auto-provisions certs.
}
```

**Traefik** (labels): enable a websocket-aware router on `/ws/live`, attach a
sticky-session service (cookie), and a Let's Encrypt resolver for TLS. Same
principles: upgrade-aware, sticky, generous timeouts.

> Whatever the proxy: (1) TLS at the edge, (2) forward upgrade headers,
> (3) long timeouts, (4) sticky by session for reconnect locality.

---

## 8. Cloud options

| Platform                     | WS fit | Notes / caveats                                                                                  |
| ---------------------------- | ------ | ------------------------------------------------------------------------------------------------ |
| **Fly.io**                   | Great  | First-class persistent connections, global Anycast, easy `fly secrets`. Run N machines; LB is WS-aware. Good default. |
| **Render**                   | Good   | Native WebSocket support on web services; managed Postgres; env groups for secrets. Straightforward. |
| **Google Cloud Run**        | ⚠️ Caveats | WebSockets supported but **request timeout caps stream lifetime** (max ~60 min) and scale-to-zero / cold starts hurt live sessions. The client's reconnect (`PROTOCOL.md` §7) masks periodic re-dials, but set max instances ≥1, raise the timeout, and prefer min-instances>0. Acceptable for bursty/demo, not ideal for always-on. |
| **AWS ECS/Fargate + ALB**   | Good   | ALB does TLS + WS upgrade and **sticky sessions** (target-group stickiness). Fargate runs the workers; RDS Postgres; ElastiCache Redis at scale. Solid, more moving parts. |
| (Kubernetes)                 | Good   | Ingress-nginx/Traefik with WS annotations + session affinity; HPA on the active-sessions metric. Use when you're already on k8s. |

**Recommendation:** Fly.io or Render for speed to prod; ECS/Fargate+ALB or k8s
when you need fine-grained scaling/affinity. Treat Cloud Run as
demo/secondary because of the stream-lifetime caps.

---

## 9. Observability stack

```mermaid
flowchart LR
  B["backend workers\nstructlog JSON → stdout\nprometheus_client → /metrics"] --> LOGS["log aggregator\n(stdout → platform/Loki)"]
  B --> PR["Prometheus\nscrape /metrics"] --> GR["Grafana\ndashboards + alerts"]
```

- **Logs:** `structlog` emits **JSON to stdout** (`logging_conf.py`), one line per
  event, keyed by `session_id` for correlation. Ship stdout to the platform's log
  pipe or Loki. Don't log media payloads or secrets.
- **Metrics (Prometheus `/metrics`):** suggested series —
  - `farryon_active_sessions` (gauge) — scaling signal.
  - `farryon_ws_messages_total{dir,kind}` — throughput.
  - `farryon_turn_latency_seconds` (histogram) — user-stop → first OUTPUT_AUDIO.
  - `farryon_tool_calls_total{name,ok}` / `farryon_tool_latency_seconds{name}`.
  - `farryon_gateway_reconnects_total{provider}` and provider error counters.
- **Grafana:** dashboards for latency p50/p95, active sessions, tool success
  rate, provider health. Alert on: p95 turn latency high, gateway error rate,
  active-sessions near capacity, `/readyz` failing.
- **Local:** all of the above is wired in `docker-compose.yml` (§2).

---

## 10. Health checks

| Endpoint   | Meaning    | Use                                                          |
| ---------- | ---------- | ----------------------------------------------------------- |
| `/healthz` | liveness   | process is up; LB/orchestrator restart probe. Cheap, no deps.|
| `/readyz`  | readiness  | dependencies OK (DB reachable, gateway configured). Gate traffic / rollout. |
| `/metrics` | scrape     | Prometheus exposition (not a health probe).                 |

- **Liveness** must not depend on Postgres/provider (or a DB blip restarts good
  pods). **Readiness** should, so a worker with a bad DB/secret is pulled from
  rotation without being killed.
- WS clients have their own app-layer heartbeat (`ping`/`pong` every 15s,
  `PROTOCOL.md` §7) independent of these HTTP probes.

---

## 11. Cost & latency considerations

**Latency (the product is "feels instant")**

- Dominant cost is the **AI provider round trip**; FarryOn adds little by keeping
  media in-process and streaming both ways in 20–100 ms chunks
  (`PROTOCOL.md` §8).
- **Co-locate** the backend in the provider's region to cut RTT. Put the edge LB
  close to users; keep app↔provider hops short.
- Stream OUTPUT_AUDIO as it arrives (don't buffer whole utterances) so the
  speaker starts in the low hundreds of ms.
- Keep video at ~1 fps / ≤1024 px — more frames mostly add provider cost &
  latency without accuracy gains.
- Track the **user-stop → first-audio** histogram as the north-star latency SLI.

**Cost**

- **Provider tokens/audio-minutes dominate.** Realtime audio in+out is the main
  bill; `web_search` provider calls add up. `AI_PROVIDER=mock` is free for
  CI/dev/demo.
- Right-size workers to active sessions; **scale to fit concurrency**, not peak
  request rate. Idle persistent connections still hold a task slot — reap dead
  ones via heartbeat.
- Postgres/Redis are minor next to provider spend at small/medium scale.
- Set per-user/session **budgets/quotas** if exposed publicly (cap minutes,
  rate-limit `web_search`).

---

## 12. Rollout & runbook

### 12.1 Deploy (rolling, zero-downtime-ish)

WebSockets can't be hot-migrated, so rollouts **drain** rather than transfer:

```mermaid
sequenceDiagram
  autonumber
  participant CI as CI/CD
  participant MIG as DB migrate
  participant NEW as New replicas
  participant LB as Load balancer
  participant OLD as Old replicas
  participant CLI as Clients

  CI->>MIG: alembic upgrade head (backward-compatible)
  CI->>NEW: deploy new image, wait /readyz
  LB->>NEW: route new connections
  LB->>OLD: stop new connections (drain)
  OLD->>OLD: SIGTERM → finish in-flight turns, then close sockets
  OLD-->>CLI: socket closes
  CLI->>LB: reconnect (backoff) + hello{resumeId}
  LB->>NEW: land on new replica, re-attach via Postgres
  CI->>OLD: terminate after drain window
```

1. **Migrate first**, backward-compatible (old code tolerates new schema).
2. Roll out new replicas; wait for `/readyz`.
3. Shift new connections to new replicas; **drain** old ones (stop accepting,
   let in-flight turns finish, then SIGTERM-close).
4. Clients reconnect (exponential backoff, `PROTOCOL.md` §7) and re-attach via
   `resumeId` + Postgres on a new replica. Brief per-session blip is expected and
   handled by the protocol.

### 12.2 Runbook (symptoms → actions)

| Symptom                                   | Likely cause                          | Action                                                                 |
| ----------------------------------------- | ------------------------------------- | ---------------------------------------------------------------------- |
| Clients reconnect-loop                    | LB killing idle sockets; no upgrade headers | Raise proxy read/idle timeout; verify `Upgrade`/`Connection` forwarding (§7). |
| High p95 turn latency                     | Provider region far / overloaded      | Co-locate to provider region; check `gateway_reconnects_total`; consider other provider. |
| `tool_result ok:false` spike              | Tool/provider outage or timeout       | Check tool/search provider; tune `TOOL_TIMEOUT_SECONDS`; mock degrades gracefully. |
| `/readyz` failing on a worker             | DB unreachable / bad secret           | Pull from rotation (readiness already does); fix `DATABASE_URL`/secret, redeploy. |
| Active sessions pegged, new connects fail | Capacity hit                          | Scale out replicas/workers; verify HPA on active-sessions metric (§4.4). |
| Reconnect loses context across workers    | Sticky off **and** state not persisted| Confirm Postgres persistence path; enable sticky (§4.2).               |
| `unauthorized` closes on connect          | JWT misconfig                         | Verify `JWT_SECRET` parity (client token signer vs server); check expiry. |
| Memory creep on a worker                  | Sessions not reaped on drop           | Confirm pump cancellation on close (`ARCHITECTURE.md` §7.3); check heartbeat reaping. |

### 12.3 Rollback

- Image: redeploy previous tag (workers are stateless; clients reconnect).
- DB: because migrations are backward-compatible, rolling back code is safe
  without a down-migration; reserve destructive migrations for a later, separate
  step once the new version is proven.
