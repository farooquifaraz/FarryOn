"""shop_stock: what is sold out, toggled from the admin panel

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "shop_stock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("in_stock", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shop_stock_key", "shop_stock", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_shop_stock_key", table_name="shop_stock")
    op.drop_table("shop_stock")
