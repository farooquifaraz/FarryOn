"""users.country: where the person was when they joined (from the phone's
timezone at signup). Nullable; older rows stay empty.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("country", sa.String(length=2), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "country")
