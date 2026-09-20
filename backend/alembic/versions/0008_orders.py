"""orders: glasses bought from the website (modules/shop)

One row per paid Stripe Checkout Session, written by the webhook. The
address and the items are JSON text — what Stripe reported, kept to ship
the parcel — never filtered on, so they are not columns.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("payment_intent", sa.String(length=128), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("address_json", sa.Text(), nullable=True),
        sa.Column("items_json", sa.Text(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="AED"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="paid"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_orders_session_id", "orders", ["session_id"], unique=True)
    op.create_index("ix_orders_email", "orders", ["email"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_created_at", "orders", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_orders_created_at", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_email", table_name="orders")
    op.drop_index("ix_orders_session_id", table_name="orders")
    op.drop_table("orders")
