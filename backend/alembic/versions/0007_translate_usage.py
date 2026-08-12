"""daily_usage: translate_seconds (live translation gets its own meter)

Live translation is billed apart from ``voice_seconds`` deliberately.

``voice_seconds`` is the *assistant's* allowance — the free plan's is a 60
minute lifetime trial. A translator is pointed at a talk or a meeting and runs
for tens of minutes at a time, so charging it to the same counter would mean
listening to one lecture leaves someone unable to speak to Farry at all, with
nothing on screen to explain which of the two ran out. Two products, two
meters.

The column is added with ``server_default="0"`` rather than backfilled: it is a
counter starting from zero, every existing row genuinely has used zero seconds
of translation, and there is no earlier value to reconstruct. That makes this
safe to apply to a live table — no rewrite, no lock, no backfill pass.

Revision ID: 0007
Revises: 0006a
Create Date: 2026-08-10
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006a"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "daily_usage",
        sa.Column(
            "translate_seconds",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    # Loses every record of translation usage. Nothing else reads the column,
    # so no other table is affected — but a downgrade-then-upgrade cycle resets
    # everyone's translate quota for the day, which on a busy day is a small
    # give-away rather than a corruption.
    op.drop_column("daily_usage", "translate_seconds")
