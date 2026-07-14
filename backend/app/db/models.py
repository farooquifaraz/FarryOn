"""SQLAlchemy ORM models for FarryOn.

These persist user-facing artifacts produced by the agent's tools (notes, tasks,
outbound messages), conversational transcripts, an audit trail of tool calls,
and the session/user records that tie everything together.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC now (used as a column default)."""
    return datetime.now(timezone.utc)


class User(Base):
    """An end user.

    Two provisioning paths coexist on this one table:

    - Anonymous glasses/app sessions: only ``external_id`` is set (the
      existing ``get_or_create_user`` flow) — ``email``/``password_hash`` stay
      NULL and the account has no admin-module role.
    - Admin/User-module accounts (signup or admin-invited): ``email`` +
      ``password_hash`` are set and one or more :class:`Role` rows are linked
      via :class:`UserRole`.
    """

    __tablename__ = "users"
    __table_args__ = (
        # Partial unique index: two soft-deleted rows may share an email, but
        # only one *live* (non-deleted) row may own it. Enforced at the DB
        # layer on both SQLite and Postgres via dialect-specific WHERE clauses.
        Index(
            "ix_users_email_live_unique",
            "email",
            unique=True,
            sqlite_where=text("email IS NOT NULL AND deleted_at IS NULL"),
            postgresql_where=text("email IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # -- Admin/User-module fields (NULL for anonymous glasses accounts) -----
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    # "active" | "invited" | "suspended" | "deactivated"
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Bumped on password reset / "log out other devices" / admin-forced
    # logout. A still-live access token is rejected once its ``iat`` predates
    # this timestamp — the DB-backed stand-in for a Redis denylist (see
    # docs/ADMIN_USER_MODULE_ARCHITECTURE.md, decision table).
    tokens_revoked_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user_roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", lazy="raise"
    )


class Role(Base):
    """A named bundle of permissions (``super_admin``, ``admin``, ``manager``, ``user``)."""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Hierarchy for guard rails ("admin can't edit/impersonate a user holding
    # a role of equal or higher level"). Higher = more privileged.
    level: Mapped[int] = mapped_column(Integer, default=0)
    # System roles (currently just super_admin) can't be renamed or deleted
    # via the admin API — only their permission set is fixed at "all".
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    role_permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", lazy="raise"
    )
    user_roles: Mapped[list["UserRole"]] = relationship(
        back_populates="role", lazy="raise"
    )


class Permission(Base):
    """A fine-grained permission string, e.g. ``users.update``."""

    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RolePermission(Base):
    """Join table: which permissions a role grants."""

    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id"), primary_key=True
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id"), primary_key=True
    )

    role: Mapped["Role"] = relationship(
        back_populates="role_permissions", lazy="raise"
    )
    permission: Mapped["Permission"] = relationship(lazy="raise")


class UserRole(Base):
    """Join table: which roles a user holds. A user may hold more than one."""

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="user_roles", lazy="raise")
    role: Mapped["Role"] = relationship(back_populates="user_roles", lazy="raise")


class RefreshToken(Base):
    """A rotating refresh token. Only its hash is stored, never the raw value.

    ``family_id`` ties together every token issued from one original login;
    rotating a token replaces it with a new row sharing the same family. If a
    *revoked* or *replaced* token is ever presented again, that's reuse of a
    stolen token — the whole family is revoked (see auth service, Phase 2).
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid4 hex
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[str] = mapped_column(String(64), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replaced_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Bumped on every rotation of this family (see auth.service.refresh) —
    # "last active" for the /me/sessions ("logout other devices") UI. NOT the
    # same as issued_at, which stays fixed at the original login time.
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Session(Base):
    """A single ``/ws/live`` connection lifecycle (supports resume)."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    resume_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Transcript(Base):
    """A finalized transcript segment (user ASR or assistant text)."""

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Note(Base):
    """A short note saved via the ``create_note`` tool."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Task(Base):
    """A to-do item created via the ``create_task`` tool."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512))
    due_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Contact(Base):
    """A saved contact for the messaging tools (WhatsApp / Telegram).

    Holds the phone number and/or Telegram handle so the user can say "WhatsApp
    Sara" and the agent resolves the destination. ``telegram_chat_id`` is filled
    in when that person starts the FarryOn bot (webhook ``/start``), enabling
    fully-automated Telegram sends.
    """

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    telegram_chat_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class OutboundMessage(Base):
    """A message queued for delivery via the ``send_message`` tool (stub)."""

    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    contact: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class ToolCall(Base):
    """Audit record for every tool invocation requested by the model."""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), index=True)
    args_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    session: Mapped["Session | None"] = relationship(lazy="raise")


class EmailVerificationToken(Base):
    """Single-use, 24h-expiry token proving ownership of ``User.email``."""

    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid4 hex
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PasswordResetToken(Base):
    """Single-use, 1h-expiry token authorizing a password reset."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid4 hex
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LoginAttempt(Base):
    """One row per login attempt — the DB-backed stand-in for Redis rate
    limiting (see docs/ADMIN_USER_MODULE_ARCHITECTURE.md, decision table).

    Queried as a windowed count (``WHERE email = ? AND created_at > now-15m``)
    to throttle both per-account and, via ``ip``, per-IP brute force.
    """

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


class TotpSecret(Base):
    """A user's TOTP secret for 2FA.

    ``enabled_at`` is NULL while the secret is "pending" (enrolled but not
    yet confirmed with a valid code — see modules/twofa/service.py::enroll).
    Only a confirmed (``enabled_at`` set) secret is checked at login.

    The secret is stored in plaintext (base32), not encrypted-at-rest —
    documented trade-off (see docs/ADMIN_USER_MODULE_ARCHITECTURE.md): TOTP
    verification needs the raw secret (unlike a password, it can't be
    one-way hashed), and this project has no KMS/app-level encryption layer
    yet. Production hardening should add one (e.g. Fernet with a
    separately-managed key) before this table holds real secrets.
    """

    __tablename__ = "totp_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    secret: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RecoveryCode(Base):
    """One single-use 2FA recovery code (10 issued per enrollment)."""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OAuthAccount(Base):
    """A linked SSO identity (Google/Microsoft) for a user.

    Linking happens by VERIFIED email match only — an unverified email on
    either side is never auto-merged into an existing account (see
    modules/sso/service.py).
    """

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        Index(
            "ix_oauth_accounts_provider_subject",
            "provider",
            "provider_user_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))  # "google" | "microsoft"
    provider_user_id: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class AuditLog(Base):
    """Append-only record of an auth event or admin mutation.

    No update/delete is ever exposed via the API (see
    app/modules/audit/router.py — GET-only) — this table is written by
    :func:`app.modules.audit.service.write_audit` and otherwise immutable.
    ``impersonator_id`` is set when the action was taken by an admin
    impersonating ``actor_id`` (see app/modules/impersonation/service.py) so
    the log always shows both identities.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    impersonator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


class Plan(Base):
    """A subscription plan (free / pro / premium).

    ``price_cents`` is an integer in the smallest currency unit — never a
    float — so revenue math is exact. ``interval`` is "month" or "year";
    MRR normalization divides yearly prices by 12 (see billing service).
    """

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    interval: Mapped[str] = mapped_column(String(8), default="month")  # month | year
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    features_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Subscription(Base):
    """A user's subscription to a :class:`Plan`.

    ``status`` lifecycle: trialing → active → past_due → canceled/expired.
    ``provider``/``provider_subscription_id`` tie back to the payment
    provider (Stripe/Razorpay/Play Billing) once one is integrated — the
    webhook receiver (modules/billing/router.py) updates rows by these ids.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # "trialing" | "active" | "past_due" | "canceled" | "expired"
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )

    user: Mapped["User"] = relationship(lazy="raise")
    plan: Mapped["Plan"] = relationship(lazy="raise")


class Payment(Base):
    """One payment/transaction — the source of truth for all revenue math.

    Refunds are recorded by flipping ``status`` to "refunded" (the webhook
    receiver does this on a provider refund event); revenue aggregation
    counts only ``succeeded`` rows.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscriptions.id"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(16), default="succeeded", index=True)
    # "succeeded" | "failed" | "refunded"
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
