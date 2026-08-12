"""daily_usage: create the per-user, per-day quota meter

This table has existed in ``models.py`` since quotas were added, but no
migration ever created it — every environment got it from ``create_all()`` at
startup, so nothing ever noticed. ``alembic upgrade head`` on a fresh database
therefore failed at 0007, which only *alters* the table:

    sqlalchemy.exc.ProgrammingError: relation "daily_usage" does not exist
    [SQL: ALTER TABLE daily_usage ADD COLUMN translate_seconds ...]

That is the first thing a real deployment does, so the gap surfaced the moment
migrations ran against an empty Postgres rather than a database ``create_all``
had already filled in.

It slots in *before* 0007 rather than after it so the chain stays honest: the
meter existed before live translation was metered, and 0007 remains the change
that added the translation counter to it. Databases already stamped at 0007 —
any dev machine where ``create_all`` made the table before alembic ran — are
unaffected: alembic replays nothing at or below the stamped revision.

Columns mirror ``DailyUsage`` exactly (counters carry Python-side defaults, as
elsewhere in this schema), minus ``translate_seconds``, which 0007 adds.

Revision ID: 0006a
Revises: 0006
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006a"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "daily_usage",
        # (user_key, day) is the composite key: user_key is the user id, or the
        # session id / "anonymous" when unauthenticated; day is YYYY-MM-DD.
        sa.Column("user_key", sa.String(64), primary_key=True, nullable=False),
        sa.Column("day", sa.String(10), primary_key=True, nullable=False),
        sa.Column("voice_seconds", sa.Integer, nullable=False),
        sa.Column("frames_sent", sa.Integer, nullable=False),
        sa.Column("text_turns", sa.Integer, nullable=False),
        sa.Column("web_searches", sa.Integer, nullable=False),
        sa.Column("image_scans", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("daily_usage")
