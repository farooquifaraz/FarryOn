"""Shared pytest fixtures.

Guarantees the suite runs with **no network and no API keys**:
- forces ``AI_PROVIDER=mock`` and ``WEB_SEARCH_PROVIDER=mock``,
- uses a shared in-memory SQLite database (one connection pool for the whole
  test so every async session sees the same schema/data),
- resets the cached settings and DB engine so each module picks up the env.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

# A temp-file SQLite DB (not :memory:) so every connection — across the
# TestClient's event-loop thread and the test's own loop — sees the same schema
# and data. Combined with NullPool in app.db.base this avoids cross-loop pooled
# connection teardown hangs while keeping the suite fully offline.
_TMP_DB = Path(tempfile.gettempdir()) / "farryon_pytest.db"
_TMP_DB.unlink(missing_ok=True)

# Set environment *before* importing application modules that read settings.
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("WEB_SEARCH_PROVIDER", "mock")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("WEB_SEARCH_API_KEY", None)

from app.config import get_settings  # noqa: E402
from app.db import base as db_base  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _settings_cache_reset() -> AsyncIterator[None]:
    """Ensure cached settings reflect the test env; clean up the DB file."""
    get_settings.cache_clear()
    yield
    _TMP_DB.unlink(missing_ok=True)


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db() -> AsyncIterator[None]:
    """Reset the schema before each test for isolation (drop + create)."""
    from app.db import models  # noqa: F401  (register models on metadata)

    engine = db_base._ensure_engine()
    async with engine.begin() as conn:
        await conn.run_sync(db_base.Base.metadata.drop_all)
        await conn.run_sync(db_base.Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Yield an :class:`AsyncSession` bound to the in-memory test database."""
    sessionmaker = db_base.get_sessionmaker()
    async with sessionmaker() as session:
        yield session
