"""Notes/tasks: client_id, updated_at, deleted_at (local-first sync, phase 1)

Adds the three columns without which notes and tasks cannot sync, and turns
deletes into tombstones:

- ``updated_at`` — so a phone can ask "what changed since I last looked?"
  instead of dragging the whole table down on every sync.
- ``deleted_at`` — so a delete can *travel*. A row that simply vanishes is
  indistinguishable from one that was never sent, so every other device keeps
  it forever. This also cost the admin panel its moderation view of anything a
  user removed.
- ``client_id`` — a UUID minted by whoever creates the row. The integer id
  can't be the sync identity: an offline phone can't ask the server for one but
  must still show the note now. It also makes a push idempotent, so an app
  killed mid-send doesn't land the same note twice.

Existing rows get ``updated_at = created_at`` (their last change *was* their
creation) and a NULL ``client_id`` — they were made before clients had one, and
the column is nullable precisely so this backfill doesn't have to invent
identities for them.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

_TABLES = ("notes", "tasks")


def upgrade() -> None:
    for table in _TABLES:
        # Added nullable, then backfilled, then made NOT NULL: adding a NOT NULL
        # column with a server_default to a live table would rewrite it and lock
        # it out for the duration.
        op.add_column(
            table, sa.Column("client_id", sa.String(length=36), nullable=True)
        )
        op.add_column(
            table,
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )

        op.execute(
            f"UPDATE {table} SET updated_at = created_at WHERE updated_at IS NULL"
        )
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "updated_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )

        # Unique so a re-sent create can't duplicate a row; indexed because
        # every pull filters on updated_at and every read filters on deleted_at.
        op.create_index(
            f"ix_{table}_client_id", table, ["client_id"], unique=True
        )
        op.create_index(f"ix_{table}_updated_at", table, ["updated_at"])
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])


def downgrade() -> None:
    # Note this loses the tombstones: rows soft-deleted while 0006 was applied
    # stay in the table, and the older code has no deleted_at to filter on, so
    # they come back to life. Nothing can be done about that from here — it is
    # the nature of undoing a soft delete — but it should be said out loud.
    for table in _TABLES:
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        op.drop_index(f"ix_{table}_updated_at", table_name=table)
        op.drop_index(f"ix_{table}_client_id", table_name=table)
        op.drop_column(table, "deleted_at")
        op.drop_column(table, "updated_at")
        op.drop_column(table, "client_id")
