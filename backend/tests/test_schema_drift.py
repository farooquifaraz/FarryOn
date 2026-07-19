"""Startup complains when the database is behind the models.

`create_all` adds tables that don't exist; it never alters ones that do. So a
database bootstrapped that way drifts silently behind the migrations, and the
first sign is a 500 from a real request:

    sqlite3.OperationalError: no such column: notes.client_id

That shipped on 2026-07-19 — the dev DB had no alembic_version, migration 0006
had never touched it, every test passed, and the first tap on Notes failed. The
warning can't fix drift (that's a decision for whoever runs the server), but it
must not let it pass in silence.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db import base as db_base
from app.db.base import get_sessionmaker, init_db


async def _drop_column(table: str, column: str) -> None:
    """Take a column away behind SQLAlchemy's back — a database from before
    the migration that added it.

    Its indexes go first: SQLite refuses to drop a column an index still names,
    and a database that never had the column never had them either.
    """
    async with get_sessionmaker()() as db:
        indexes = (
            await db.execute(text(f"PRAGMA index_list({table})"))
        ).all()
        for row in indexes:
            name = row[1]
            cols = {
                r[2]
                for r in (await db.execute(text(f"PRAGMA index_info({name})"))).all()
            }
            if column in cols:
                await db.execute(text(f"DROP INDEX IF EXISTS {name}"))
        await db.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        await db.commit()


@pytest.fixture
def drift_log(capsys):
    """What init_db printed.

    structlog is configured with a PrintLoggerFactory, so its output goes to
    stdout rather than through the stdlib logging that `caplog` captures — the
    first draft of these tests watched the wrong stream and passed nothing.
    """

    def _read() -> str:
        return capsys.readouterr().out

    return _read


async def test_a_missing_column_is_reported(drift_log) -> None:
    await _drop_column("notes", "client_id")
    await init_db()

    logged = drift_log()
    assert "db.schema_drift" in logged, "drift passed in silence"
    assert "client_id" in logged, "the message must name the column"
    assert "alembic" in logged, "and say what to do about it"


async def test_a_healthy_schema_says_nothing(drift_log) -> None:
    # The conftest builds the schema from the models, so it is by definition
    # in step. A warning here would be noise on every start, and noise is how
    # a real warning gets ignored.
    await init_db()

    assert "db.schema_drift" not in drift_log()


async def test_the_check_never_alters_anything(drift_log) -> None:
    # It reports; it does not repair. Silently ALTERing a production table at
    # startup is a worse habit than the drift it would be hiding.
    await _drop_column("tasks", "deleted_at")
    await init_db()

    async with get_sessionmaker()() as db:
        cols = {
            r[1]
            for r in (await db.execute(text("PRAGMA table_info(tasks)"))).all()
        }
    assert "deleted_at" not in cols, "the check repaired the schema on its own"
    assert "db.schema_drift" in drift_log()


async def test_a_brand_new_database_is_quiet(drift_log) -> None:
    # create_all has just built every table from the same models, so nothing
    # can be missing. This is the ordinary path and it must stay silent.
    async with db_base._ensure_engine().begin() as conn:
        await conn.run_sync(db_base.Base.metadata.drop_all)
    await init_db()

    assert "db.schema_drift" not in drift_log()
