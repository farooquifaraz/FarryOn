"""FastAPI application factory and ASGI entrypoint.

Run with::

    uvicorn app.main:app --reload

Wires up logging, the database bootstrap (``create_all``), CORS, the
``/ws/live`` WebSocket route, and operational endpoints ``/healthz`` (liveness),
``/readyz`` (readiness incl. DB ping), and ``/metrics`` (Prometheus).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.deps import get_data_owner
from app.core.responses import AppError, app_error_handler
from app.core.tls import sanitize_ca_env
from app.db import repo
from app.db.base import dispose_db, get_sessionmaker, init_db
from app.db.models import User
from app.logging_conf import configure_logging, get_logger
from app.modules.audit.router import router as audit_router
from app.modules.billing.router import router as billing_router
from app.modules.billing.router import webhook_router as billing_webhook_router
from app.modules.auth.router import me_router as auth_me_router
from app.modules.auth.router import router as auth_router
from app.modules.impersonation.router import router as impersonation_router
from app.modules.rbac.router import router as rbac_router
from app.modules.sessions.router import admin_router as sessions_admin_router
from app.modules.sessions.router import me_router as sessions_me_router
from app.modules.sso.router import router as sso_router
from app.modules.twofa.router import admin_router as twofa_admin_router
from app.modules.twofa.router import me_router as twofa_me_router
from app.modules.users.router import router as users_router
from app.services.vision import run_detection
from app.ws.live import router as ws_router

logger = get_logger(__name__)


class DetectRequest(BaseModel):
    """Body for ``POST /detect`` (the Finder screen's request).

    Exactly one image source is expected: ``image_data`` (a base64 / data-URL
    string from a file or camera capture) or ``image_url`` (a public URL).
    API keys are NOT accepted here — they live server-side in settings.
    """

    mode: str = "auto"
    image_data: str | None = None
    image_url: str | None = None
    # BCP-47 language code (device locale) so the product AI explanation comes
    # back in the user's language. Optional; defaults to English server-side.
    lang: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup/shutdown: configure logging and the database."""
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    logger.info(
        "app.starting",
        ai_provider=settings.ai_provider,
        database=settings.database_url.split("://", 1)[0],
    )
    await init_db(settings)
    try:
        yield
    finally:
        await dispose_db()
        logger.info("app.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct and configure the FastAPI application.

    Args:
        settings: Optional settings override (defaults to the cached global).

    Returns:
        A configured :class:`fastapi.FastAPI` instance.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    # Before anything can make an HTTPS call: drop a CA-bundle env var that
    # points at a file the machine doesn't have. See app/core/tls.py — this is
    # what made Google sign-in fail with "Couldn't reach Google" on a perfectly
    # healthy phone.
    sanitize_ca_env()

    app = FastAPI(
        title="FarryOn Backend",
        version="1.0.0",
        description="Realtime multimodal AI assistant (voice + vision + tools).",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # SessionMiddleware: required by authlib's Starlette OAuth client to
    # store CSRF state between /auth/sso/{provider}/login and .../callback.
    # Harmless when SSO is unconfigured — the routes themselves 503 before
    # touching the session in that case (see modules/sso/router.py).
    app.add_middleware(
        SessionMiddleware, secret_key=settings.session_secret or settings.jwt_secret
    )

    # Hardening (admin/user module Phase 9): request-id correlation in every
    # log line + response header, and standard security headers everywhere.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(ws_router)

    # Admin/User module — additive only: new prefix, own error envelope
    # (AppError -> app_error_handler), zero effect on any existing route.
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(auth_me_router, prefix="/api/v1")
    app.include_router(rbac_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(sessions_me_router, prefix="/api/v1")
    app.include_router(sessions_admin_router, prefix="/api/v1")
    app.include_router(twofa_me_router, prefix="/api/v1")
    app.include_router(twofa_admin_router, prefix="/api/v1")
    app.include_router(sso_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(impersonation_router, prefix="/api/v1")
    app.include_router(billing_router, prefix="/api/v1")
    app.include_router(billing_webhook_router, prefix="/api/v1")

    @app.get("/healthz", response_class=JSONResponse, tags=["ops"])
    async def healthz() -> JSONResponse:
        """Liveness probe — process is up and serving."""
        return JSONResponse(
            {
                "status": "ok",
                "provider": settings.ai_provider,
                "protocolVersion": 1,
            }
        )

    @app.get("/readyz", response_class=JSONResponse, tags=["ops"])
    async def readyz() -> JSONResponse:
        """Readiness probe — dependencies are reachable (DB ping).

        Returns 200 when the service can take traffic and 503 otherwise, so
        load balancers and rollout tooling can gate traffic / pull a worker
        from rotation when its database is unreachable.
        """
        checks: dict[str, str] = {"gateway": settings.ai_provider}
        ready = True
        try:
            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - report any failure as not-ready
            ready = False
            checks["database"] = f"error: {exc.__class__.__name__}"
            logger.warning("readyz.db_unreachable", error=str(exc))
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "checks": checks},
            status_code=200 if ready else 503,
        )

    @app.get("/metrics", tags=["ops"])
    async def metrics_endpoint() -> PlainTextResponse:
        """Prometheus metrics in the text exposition format."""
        return PlainTextResponse(
            generate_latest(), media_type=CONTENT_TYPE_LATEST
        )

    @app.get("/", tags=["ops"])
    async def root() -> JSONResponse:
        """Friendly service banner."""
        return JSONResponse(
            {
                "service": "FarryOn Backend",
                "ws": "/ws/live",
                "health": "/healthz",
                "ready": "/readyz",
                "metrics": "/metrics",
            }
        )

    # ---- Notes & tasks (read/manage what the agent created) ----------------
    #
    # Every one of these six is scoped to `owner` — the caller resolved from the
    # Bearer header by core/deps.py::get_data_owner. Two rules hold across all
    # of them, and both are load-bearing:
    #
    #   * Reads pass `user_id=owner.id`. Omitting it lists the whole table.
    #   * Writes pass it too. The ids here are small integers straight off the
    #     client, so ownership can't be inferred from "they knew the id" — a
    #     signed-in stranger can ask for note 4. repo.delete_note & friends
    #     answer "not found" rather than "not yours": a 403 would confirm the
    #     row exists, which is itself a leak.
    #
    # The rows themselves get their owner upstream, at the live session's
    # handshake (ws/session.py::_resolve_owner), so what the agent creates and
    # what these endpoints return agree on who it belongs to.

    @app.get("/notes", tags=["data"])
    async def list_notes_endpoint(
        owner: User = Depends(get_data_owner),
    ) -> JSONResponse:
        """List the caller's saved notes (newest first) for the app's Notes view."""
        async with get_sessionmaker()() as db:
            notes = await repo.list_notes(db, user_id=owner.id, limit=200)
            return JSONResponse(
                [
                    {
                        "id": n.id,
                        "text": n.text,
                        "createdAt": n.created_at.isoformat(),
                    }
                    for n in notes
                ]
            )

    @app.get("/tasks", tags=["data"])
    async def list_tasks_endpoint(
        owner: User = Depends(get_data_owner),
    ) -> JSONResponse:
        """List the caller's tasks (open first, then newest) for the Tasks view."""
        async with get_sessionmaker()() as db:
            tasks = await repo.list_tasks(
                db, user_id=owner.id, include_done=True, limit=200
            )
            return JSONResponse(
                [
                    {
                        "id": t.id,
                        "title": t.title,
                        "dueDate": t.due_date,
                        "done": t.done,
                        "createdAt": t.created_at.isoformat(),
                    }
                    for t in tasks
                ]
            )

    @app.post("/tasks/{task_id}/done", tags=["data"])
    async def set_task_done_endpoint(
        task_id: int,
        done: bool = True,
        owner: User = Depends(get_data_owner),
    ) -> JSONResponse:
        """Mark one of the caller's tasks done/undone (``?done=true|false``)."""
        async with get_sessionmaker()() as db:
            task = await repo.set_task_done(
                db, task_id=task_id, done=done, user_id=owner.id
            )
            await db.commit()
            if task is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            return JSONResponse({"id": task.id, "done": task.done})

    @app.delete("/notes/{note_id}", tags=["data"])
    async def delete_note_endpoint(
        note_id: int, owner: User = Depends(get_data_owner)
    ) -> JSONResponse:
        """Delete one of the caller's notes."""
        async with get_sessionmaker()() as db:
            ok = await repo.delete_note(db, note_id=note_id, user_id=owner.id)
            await db.commit()
            return JSONResponse(
                {"deleted": ok}, status_code=200 if ok else 404
            )

    @app.delete("/tasks/{task_id}", tags=["data"])
    async def delete_task_endpoint(
        task_id: int, owner: User = Depends(get_data_owner)
    ) -> JSONResponse:
        """Delete one of the caller's tasks."""
        async with get_sessionmaker()() as db:
            ok = await repo.delete_task(db, task_id=task_id, user_id=owner.id)
            await db.commit()
            return JSONResponse(
                {"deleted": ok}, status_code=200 if ok else 404
            )

    # ---- Image understanding (landmark & product finder) -------------------

    @app.post("/detect", tags=["vision"])
    async def detect_endpoint(req: DetectRequest) -> JSONResponse:
        """Identify a landmark or product in an uploaded image.

        Mirrors the standalone finder's contract — returns the
        ``{ok, mode, result}`` envelope — but reads API keys from server config
        instead of the request body. ``mode`` is ``auto`` | ``landmark`` |
        ``product`` | ``web``. Used by the app's Finder screen and live-scan.
        """
        envelope = await run_detection(
            req.mode,  # type: ignore[arg-type]
            settings=settings,
            image_data=req.image_data,
            image_url=req.image_url,
            lang=req.lang,
        )
        return JSONResponse(envelope)

    @app.post("/webhook/telegram", tags=["messaging"])
    async def telegram_webhook(request: Request) -> JSONResponse:
        """Telegram pushes incoming bot messages here.

        On ``/start`` we save the sender's ``chat_id`` so the agent can later
        message them automatically, and reply with a welcome. Always returns
        ``{ok: true}`` so Telegram doesn't retry.
        """
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": True})
        msg = data.get("message") or data.get("edited_message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        text_msg = (msg.get("text") or "").strip()
        if chat_id and text_msg.startswith("/start"):
            try:
                async with get_sessionmaker()() as db:
                    await repo.upsert_telegram_chat(
                        db, chat_id=str(chat_id),
                        username=chat.get("username"),
                        display_name=chat.get("first_name"),
                    )
                    await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram_webhook.save_failed", error=str(exc))
            token = settings.telegram_bot_token
            if token:
                import httpx
                name = chat.get("first_name", "")
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": f"Salaam {name}! You're connected to "
                                "FarryOn — I can message you here now.",
                            },
                        )
                except Exception as exc:  # noqa: BLE001 - reply is best-effort
                    logger.warning(
                        "telegram_webhook.start_reply_failed", error=str(exc)
                    )
        return JSONResponse({"ok": True})

    return app


#: The ASGI application object referenced by ``uvicorn app.main:app``.
app = create_app()
