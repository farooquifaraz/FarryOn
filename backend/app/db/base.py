"""Async SQLAlchemy engine, session factory, and schema bootstrap.

Uses SQLAlchemy 2.0's async ORM. The engine is created lazily from
:class:`~app.config.Settings` so importing this module never touches the
database. ``aiosqlite`` backs local/dev/CI; switch ``DATABASE_URL`` to an
``asyncpg`` URL for Postgres.

Migrations: dev/CI keep using :func:`init_db` / ``create_all`` (fast, no
migration step for throwaway SQLite). Production Postgres is schema-managed by
Alembic instead — see ``backend/alembic/`` (``alembic upgrade head``);
``env.py`` targets this module's :data:`Base.metadata` via an async engine, so
there's no separate sync-driver connection string to maintain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Module-level singletons, initialised on first use via :func:`_ensure_engine`.
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _ensure_engine(settings: Settings | None = None) -> AsyncEngine:
    """Create (once) and return the process-wide async engine.

    For SQLite (aiosqlite) we use :class:`~sqlalchemy.pool.NullPool` so each
    operation opens and closes its own connection. This keeps the async driver
    robust when sessions span multiple event loops/threads (e.g. Starlette's
    threaded ``TestClient`` portal vs. the test's own loop) — a pooled
    connection created on one loop must never be finalized on another.
    """
    global _engine, _sessionmaker
    if _engine is None:
        settings = settings or get_settings()
        is_sqlite = settings.database_url.startswith("sqlite")
        kwargs: dict[str, object] = {"echo": False, "future": True}
        if is_sqlite:
            # ``check_same_thread`` only matters for SQLite; NullPool avoids
            # cross-loop pooled-connection teardown hangs.
            kwargs["poolclass"] = NullPool
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_pre_ping"] = True
        _engine = create_async_engine(settings.database_url, **kwargs)
        _sessionmaker = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory, initialising the engine if needed."""
    if _sessionmaker is None:
        _ensure_engine()
    assert _sessionmaker is not None  # for type-checkers; set by _ensure_engine
    return _sessionmaker


async def init_db(settings: Settings | None = None) -> None:
    """Create all tables if they do not yet exist (``create_all`` bootstrap).

    Importing :mod:`app.db.models` here guarantees every model is registered on
    :data:`Base.metadata` before ``create_all`` runs.
    """
    # Imported for side effect: registers models on Base.metadata.
    from app.db import models  # noqa: F401

    engine = _ensure_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    """Dispose the engine and reset module singletons (used on shutdown/tests)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency / context helper yielding an :class:`AsyncSession`.

    Commits on success, rolls back on exception, and always closes the session.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
