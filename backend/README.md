# FarryOn Backend

Python · FastAPI · native WebSockets — the realtime brain behind FarryOn. It
terminates the `/ws/live` socket defined in [`../PROTOCOL.md`](../PROTOCOL.md),
streams audio/video to a realtime multimodal model through a pluggable
**AIGateway**, runs **agentic tool calls**, and persists results.

> The wire contract in `../PROTOCOL.md` is authoritative. This service conforms
> to it exactly (message `type`s, binary tags `0x01/0x02/0x03`, the 9-byte
> binary header, and the tool names `create_note` / `web_search` /
> `create_task` / `send_message`).

## Quick start

```bash
cd backend
cp .env.example .env                    # safe defaults; mock provider needs no keys
python -m pip install -r requirements.txt
uvicorn app.main:app --reload           # ws://localhost:8000/ws/live
```

With zero configuration the service runs the deterministic **mock** AI provider
(no network, no keys) — ideal for local UI development and CI. Point a client at
`ws://localhost:8000/ws/live`, send `hello` + `config`, and you'll get a `ready`
plus a scripted turn (transcript → `create_note` tool call → streamed audio).

Operational endpoints:

- `GET /healthz` — liveness probe (`{"status":"ok",...}`)
- `GET /metrics` — Prometheus exposition
- `GET /docs` — OpenAPI UI for the HTTP routes

## Running the tests

The suite is **fully offline** — no API keys, in-memory/temp-file SQLite, mock
provider:

```bash
cd backend
python -m pip install -r requirements.txt -q
python -m pytest -q
```

## Configuration

All config is environment-driven (see `.env.example` for the annotated list).

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_PROVIDER` | `mock` | `gemini` \| `openai` \| `mock` |
| `GEMINI_API_KEY` | – | Required when `AI_PROVIDER=gemini` |
| `GEMINI_MODEL` | `gemini-2.5-flash-native-audio-latest` | Gemini Live model id |
| `OPENAI_API_KEY` | – | Required when `AI_PROVIDER=openai` |
| `OPENAI_REALTIME_MODEL` | `gpt-realtime` | Realtime model id |
| `DATABASE_URL` | `sqlite+aiosqlite:///./farryon.db` | Async SQLAlchemy URL |
| `WEB_SEARCH_PROVIDER` | `mock` | `mock` \| `tavily` \| `serpapi` |
| `WEB_SEARCH_API_KEY` | – | Key for the chosen search provider |
| `LOG_LEVEL` | `INFO` | Root log level |
| `JWT_SECRET` | `dev-insecure-change-me` | HS256 secret for `?token=`; auth is enforced only when changed from the default |
| `ALLOWED_ORIGINS` | `*` | CORS allow-list (comma-separated) |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address |
| `TOOL_TIMEOUT_SECONDS` | `20` | Per-tool execution timeout |

## Architecture

```
app/
  main.py            FastAPI factory, lifespan (logging + DB bootstrap), /healthz, /metrics
  config.py          pydantic-settings Settings (env)
  logging_conf.py    structlog JSON logging
  ws/
    frames.py        9-byte binary header codec (tag + LE uint64 ts) + helpers
    live.py          /ws/live endpoint: optional JWT, builds gateway+engine, runs Session
    session.py       Session: handshake, read-pump + event-pump, barge-in, teardown
  ai/
    base.py          AIGateway ABC (transport-agnostic provider contract)
    events.py        typed GatewayEvent dataclasses (transcript/audio/tool_call/...)
    mock.py          deterministic, network-free gateway (tests/demos)
    gemini.py        Gemini Live adapter (google-genai); guarded import
    openai_realtime.py  OpenAI Realtime adapter (openai); guarded import
    factory.py       build_gateway() from settings
  agent/
    tool_engine.py   registry, schema export, validation, dispatch (timeout + error capture)
    orchestrator.py  model tool-call loop: notify UI -> run -> audit -> feed result back
  tools/
    base.py          Tool ABC + ToolContext
    notes.py tasks.py web_search.py messaging.py   the four canonical tools
    __init__.py      build_default_tools() registry
  db/
    base.py          async engine/sessionmaker, Base, init_db() create_all bootstrap
    models.py        User, Session, Note, Task, OutboundMessage, ToolCall, Transcript
    repo.py          repository helpers used by tools/session
  prompts/system.py  SYSTEM_PROMPT + tool-routing guidance
  observability/metrics.py   Prometheus counters/gauges/histograms
```

### Request flow (one turn)

1. Client opens `/ws/live`, sends `hello` (+ optional `config`).
2. `Session` builds the configured gateway, emits `ready`, sets state.
3. Client streams `0x01` audio / `0x02` video binary frames and/or `text`.
   The **read pump** decodes frames and forwards them to the gateway.
4. The gateway emits `GatewayEvent`s; the **event pump** translates them into
   `transcript`, `audio_start` + `0x03` audio frames + `audio_end`, and
   `state` messages.
5. On a `tool_call` event the **orchestrator** notifies the UI (`tool_call`),
   runs the tool via the **tool engine** (validated, timed, error-captured),
   writes a `ToolCall` audit row, notifies the UI (`tool_result`), and feeds the
   result back to the model to finish the turn.
6. A client `interrupt` triggers barge-in (`gateway.interrupt()`); disconnect
   cancels both pumps and tears the gateway down cleanly.

## How to add a tool

1. Implement `app/tools/base.py::Tool` in a new module: set `name`,
   `description`, and `parameters` (JSON-Schema), and write `async def run`.
2. Register it in `app/tools/__init__.py::build_default_tools`.

It is then auto-exported to the model for function calling and dispatchable by
the engine. If the tool is part of the public contract, also add it to
`../PROTOCOL.md` section 5.

## How to add an AI provider

1. Implement `app/ai/base.py::AIGateway` in a new module (e.g.
   `app/ai/<provider>.py`). Keep the SDK import **inside `connect()`** so the
   app imports cleanly without the dependency installed.
2. Normalize the provider stream into `app/ai/events.py` events.
3. Wire it into `app/ai/factory.py::build_gateway` and add the enum value to
   `Settings.ai_provider` (`AIProvider` in `app/config.py`).

## Database & migrations

Dev/CI use SQLite via `aiosqlite`; production uses Postgres via `asyncpg`
(`DATABASE_URL=postgresql+asyncpg://...`). Schema is bootstrapped with a simple
`create_all` in `app/db/base.py::init_db` (run during app startup). For
production schema evolution, introduce **Alembic**: point `alembic.ini`
`sqlalchemy.url` at the sync driver (`postgresql://...`) and set
`target_metadata = app.db.base.Base.metadata` in `env.py`. (No Alembic files are
shipped here to keep the bootstrap dependency-free.)

## Known limitation: the data endpoints are single-user

`GET /notes`, `GET /tasks`, `POST /tasks/{id}/done`, `DELETE /notes/{id}` and
`DELETE /tasks/{id}` are **unauthenticated and not scoped to a user** — the
reads list the whole table and the writes take a bare row id without any
ownership check.

This is harmless today because there is exactly one user: every live session
resolves to the shared `_ANON_USER` row
(`app/ws/session.py::_persist_session_start`), so all notes and tasks already
carry the same `user_id`. Adding a `user_id=` filter to the reads would
therefore be cosmetic.

The actual gap is that the client sends **no identity at all** — no token and
no device id, on either the WS `hello` or these REST calls. Closing it needs,
in order:

1. a per-install (or per-account) identity on the client, sent on `hello` and
   as a credential on the REST calls;
2. `_ANON_USER` replaced by that identity in `_persist_session_start`;
3. these endpoints resolving the caller and scoping every read *and* write by
   it — `app/core/deps.py::get_current_user` already does step 3 for
   `Authorization: Bearer` tokens.

**Fix this before the admin/user module serves more than one real account**,
and pick the identity model first (a device-scoped id keeps the current
no-login UX; the admin module's JWT gives real auth but needs a login flow in
the app). Deferred deliberately on 2026-07-14.

## Docker

```bash
docker build -t farryon-backend ./backend
docker run --rm -p 8000:8000 --env-file backend/.env farryon-backend
```

Runs as a non-root user on `python:3.11-slim` with a `/healthz` HEALTHCHECK.
