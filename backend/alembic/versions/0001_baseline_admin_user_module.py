"""Baseline schema + admin/user module (roles, permissions, refresh tokens)

This is the FIRST Alembic revision in this project. Dev/CI continue to use
the ``create_all()`` bootstrap in ``app/db/base.py`` unchanged (fast, no
migration step needed for throwaway SQLite). This revision exists to bring a
*fresh* production Postgres database up to the current full schema in one
step via ``alembic upgrade head`` — it is not meant to be run against a
database that already has these tables from ``create_all()``.

Revision ID: 0001
Revises:
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    # ---- pre-existing application tables (previously create_all-only) -----
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("locale", sa.String(16), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tokens_revoked_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_external_id", "users", ["external_id"], unique=True)
    op.create_index(
        "ix_users_email_live_unique",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL AND deleted_at IS NULL"),
        sqlite_where=sa.text("email IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resume_of", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("client_platform", sa.String(32), nullable=True),
        sa.Column("device_kind", sa.String(32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "transcripts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transcripts_session_id", "transcripts", ["session_id"])

    op.create_table(
        "notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notes_user_id", "notes", ["user_id"])
    op.create_index("ix_notes_session_id", "notes", ["session_id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("due_date", sa.String(64), nullable=True),
        sa.Column("done", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_session_id", "tasks", ["session_id"])

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("telegram_username", sa.String(64), nullable=True),
        sa.Column("telegram_chat_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contacts_user_id", "contacts", ["user_id"])
    op.create_index("ix_contacts_name", "contacts", ["name"])
    op.create_index("ix_contacts_telegram_chat_id", "contacts", ["telegram_chat_id"])

    op.create_table(
        "outbound_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("contact", sa.String(255), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outbound_messages_user_id", "outbound_messages", ["user_id"])
    op.create_index("ix_outbound_messages_session_id", "outbound_messages", ["session_id"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("args_json", sa.Text, nullable=False),
        sa.Column("result_json", sa.Text, nullable=True),
        sa.Column("ok", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_calls_call_id", "tool_calls", ["call_id"])
    op.create_index("ix_tool_calls_session_id", "tool_calls", ["session_id"])
    op.create_index("ix_tool_calls_name", "tool_calls", ["name"])

    # ---- admin/user module ------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
    )
    op.create_index("ix_permissions_code", "permissions", ["code"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer, sa.ForeignKey("roles.id"), primary_key=True),
        sa.Column(
            "permission_id", sa.Integer, sa.ForeignKey("permissions.id"), primary_key=True
        ),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role_id", sa.Integer, sa.ForeignKey("roles.id"), primary_key=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("tool_calls")
    op.drop_table("outbound_messages")
    op.drop_table("contacts")
    op.drop_table("tasks")
    op.drop_table("notes")
    op.drop_table("transcripts")
    op.drop_table("sessions")
    op.drop_table("users")
