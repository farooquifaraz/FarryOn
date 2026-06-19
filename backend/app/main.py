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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db.base import dispose_db, get_sessionmaker, init_db
from app.logging_conf import configure_logging, get_logger
from app.ws.live import router as ws_router

logger = get_logger(__name__)


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

    app.include_router(ws_router)

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

    return app


#: The ASGI application object referenced by ``uvicorn app.main:app``.
app = create_app()
