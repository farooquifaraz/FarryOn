"""shop_prices: glasses and delivery prices set from the admin panel

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "shop_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_shop_prices_key_currency", "shop_prices", ["key", "currency"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_shop_prices_key_currency", table_name="shop_prices")
    op.drop_table("shop_prices")
