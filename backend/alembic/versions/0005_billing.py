"""Billing: plans, subscriptions, payments

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("price_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("interval", sa.String(8), nullable=False, server_default="month"),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("features_json", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plans_name", "plans", ["name"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("provider_subscription_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"])
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])
    op.create_index(
        "ix_subscriptions_provider_subscription_id",
        "subscriptions",
        ["provider_subscription_id"],
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "subscription_id", sa.Integer, sa.ForeignKey("subscriptions.id"), nullable=True
        ),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(16), nullable=False, server_default="succeeded"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("provider_payment_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_payments_subscription_id", "payments", ["subscription_id"])
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_index("ix_payments_paid_at", "payments", ["paid_at"])
    op.create_index(
        "ix_payments_provider_payment_id", "payments", ["provider_payment_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("subscriptions")
    op.drop_table("plans")
