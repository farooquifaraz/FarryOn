"""users: which app build each person runs, and the upgrade nudge

Revision ID: 0013
Revises: 0011
Create Date: 2026-09-30

Two things the admin could not see (Faraz, 2026-09-29): which build of the
app a user is on — the hello carries it, the log kept it, the database did
not — and who has used up their talk time without upgrading, and whether
they were told. The nudge columns make "one upgrade email per person every
seven days, and never after they opted out" a fact of the row rather than
a hope.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_app_build", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("last_app_platform", sa.String(length=16), nullable=True)
        )
        batch.add_column(
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("upgrade_nudged_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "upgrade_nudge_opt_out",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("upgrade_nudge_opt_out")
        batch.drop_column("upgrade_nudged_at")
        batch.drop_column("last_seen_at")
        batch.drop_column("last_app_platform")
        batch.drop_column("last_app_build")
