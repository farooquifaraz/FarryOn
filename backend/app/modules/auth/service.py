"""Core auth business logic: register, login, refresh rotation, logout,
email verification, password reset.

Security notes (see docs/ADMIN_USER_MODULE_ARCHITECTURE.md for the full
decision record):

- Passwords: argon2id via :mod:`app.core.security`.
- Refresh tokens: opaque random values, only their SHA-256 hash is stored.
  argon2 is deliberately NOT used here — these are high-entropy random
  tokens (not user-chosen secrets), so a fast hash is correct and avoids
  turning every refresh into a ~100ms argon2 call.
- Refresh rotation + reuse detection: each refresh consumes the current
  token and issues a new one in the same ``family_id``. If a token that was
  already revoked/replaced is presented again, that's a stolen-token replay
  — the entire family is revoked, forcing re-login on every device sharing
  that login.
- No user enumeration: login and forgot-password return the same generic
  response whether or not the email exists.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.rate_limit import is_locked_out, record_attempt
from app.core.responses import AppError
from app.core.security import (
    decode_token,
    encode_token,
    hash_opaque_token,
    hash_password,
    new_opaque_token,
    verify_password,
)
from app.db.models import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
    User,
)
from app.modules.auth import notifications
from app.modules.auth.schemas import TokenPairResponse, TwoFactorRequiredResponse
from app.modules.twofa import service as twofa_service
from app.logging_conf import get_logger

logger = get_logger(__name__)

EMAIL_VERIFICATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)
RESEND_COOLDOWN = timedelta(seconds=60)
PENDING_2FA_TTL = timedelta(minutes=5)

# Local aliases: these are generic opaque-token utilities that live in
# core/security.py (shared with modules/users/service.py's invite flow),
# kept under their original names here so the rest of this file is unchanged.
_hash_opaque_token = hash_opaque_token
_new_opaque_token = new_opaque_token


def _naive(dt: datetime) -> datetime:
    """Strip tzinfo for cross-dialect comparison.

    SQLite has no native timezone type — even a ``DateTime(timezone=True)``
    column round-trips as a naive datetime via aiosqlite, while Postgres
    (asyncpg) keeps it timezone-aware. Stripping tzinfo on *both* sides of
    every comparison keeps the same code correct on both dialects.
    """
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


async def _issue_token_pair(
    db: AsyncSession,
    settings: Settings,
    *,
    user: User,
    family_id: str | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPairResponse:
    """Mint an access token + a brand-new refresh token row."""
    resolved_family_id = family_id or uuid.uuid4().hex
    access = encode_token(
        settings=settings,
        user_id=user.id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        # "fid" (session/device family) is read only by GET /me/sessions to
        # mark which listed session is the caller's current one — it plays
        # no role in authentication itself.
        extra_claims={"fid": resolved_family_id},
    )

    raw_refresh = _new_opaque_token()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    db.add(
        RefreshToken(
            id=uuid.uuid4().hex,
            user_id=user.id,
            family_id=resolved_family_id,
            token_hash=_hash_opaque_token(raw_refresh),
            issued_at=now,
            last_used_at=now,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
    )
    await db.flush()

    return TokenPairResponse(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def issue_token_pair_for_sso(
    db: AsyncSession,
    settings: Settings,
    *,
    user: User,
    user_agent: str | None,
    ip: str | None,
) -> TokenPairResponse:
    """Public entry point for modules/sso/router.py — a successful OIDC
    callback is just another way to establish a session, same as login()."""
    return await _issue_token_pair(db, settings, user=user, user_agent=user_agent, ip=ip)


async def register(
    db: AsyncSession, settings: Settings, *, email: str, password: str, display_name: str | None
) -> User:
    email_norm = email.lower()
    existing = (
        await db.execute(select(User).where(User.email == email_norm))
    ).scalar_one_or_none()
    if existing is not None and existing.deleted_at is None:
        # Same generic shape as any other validation error — but this one
        # DOES reveal the email is taken (unlike login/forgot-password). The
        # register endpoint's whole purpose is claiming an address, so a
        # generic response here would just break the UX for no security
        # benefit (an attacker learns nothing they couldn't learn by trying
        # to log in, which IS enumeration-safe).
        raise AppError(
            "EMAIL_TAKEN", "An account with this email already exists.",
            status_code=409, fields={"email": "already registered"},
        )

    user = User(
        # A random id, NOT derived from email: email is freed for reuse on
        # soft delete (see the partial unique index on User.email), but
        # external_id's constraint has no such carve-out — deriving it from
        # email would permanently block re-registering a deleted address.
        external_id=f"user:{uuid.uuid4().hex}",
        email=email_norm,
        password_hash=hash_password(password),
        display_name=display_name,
        status="active",
    )
    db.add(user)
    await db.flush()

    await _issue_email_verification(db, settings, user)
    logger.info("auth.register", user_id=user.id)
    return user


async def _issue_email_verification(
    db: AsyncSession, settings: Settings, user: User
) -> None:
    recent = (
        await db.execute(
            select(EmailVerificationToken)
            .where(EmailVerificationToken.user_id == user.id)
            .order_by(EmailVerificationToken.created_at.desc())
        )
    ).scalars().first()
    now = datetime.now(timezone.utc)
    if (
        recent is not None
        and recent.consumed_at is None
        and _naive(now) - _naive(recent.created_at) < RESEND_COOLDOWN
    ):
        raise AppError(
            "RESEND_TOO_SOON", "Please wait a bit before requesting another email.",
            status_code=429,
        )

    raw = _new_opaque_token()
    db.add(
        EmailVerificationToken(
            id=uuid.uuid4().hex,
            user_id=user.id,
            token_hash=_hash_opaque_token(raw),
            email=user.email,
            expires_at=now + EMAIL_VERIFICATION_TTL,
        )
    )
    await db.flush()
    notifications.send_verification_email(to_email=user.email, token=raw)


async def verify_email(db: AsyncSession, *, raw_token: str) -> None:
    token_hash = _hash_opaque_token(raw_token)
    row = (
        await db.execute(
            select(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == token_hash
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.consumed_at is not None or _naive(row.expires_at) < _naive(now):
        raise AppError("INVALID_TOKEN", "This verification link is invalid or expired.", status_code=400)

    row.consumed_at = now
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one()
    user.email_verified_at = now
    await db.flush()
    logger.info("auth.email_verified", user_id=user.id)


async def login(
    db: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> TokenPairResponse | TwoFactorRequiredResponse:
    email_norm = email.lower()

    if await is_locked_out(db, email=email_norm, ip=ip):
        raise AppError(
            "TOO_MANY_ATTEMPTS",
            "Too many failed attempts. Try again later.",
            status_code=429,
        )

    user = (
        await db.execute(select(User).where(User.email == email_norm))
    ).scalar_one_or_none()

    # Constant-shape failure path: verify against a real hash when the user
    # exists, otherwise against a fixed dummy hash — so response timing
    # doesn't leak whether the email exists (verify_password is
    # constant-time internally; this just ensures we always call it).
    password_ok = False
    if user is not None and user.password_hash is not None:
        password_ok = verify_password(password, user.password_hash)
    else:
        verify_password(password, _DUMMY_HASH)

    valid_account = (
        user is not None
        and user.deleted_at is None
        and user.password_hash is not None
        and password_ok
    )

    await record_attempt(db, email=email_norm, ip=ip, success=valid_account)

    if not valid_account:
        raise AppError(
            "INVALID_CREDENTIALS", "Incorrect email or password.", status_code=401
        )

    assert user is not None
    if user.status in ("suspended", "deactivated"):
        raise AppError(
            "USER_SUSPENDED", "This account is no longer active.", status_code=403
        )

    if await twofa_service.is_enabled(db, user.id):
        pending = encode_token(
            settings=settings,
            user_id=user.id,
            token_type="2fa_pending",
            expires_delta=PENDING_2FA_TTL,
        )
        logger.info("auth.login_pending_2fa", user_id=user.id)
        return TwoFactorRequiredResponse(pending_token=pending)

    logger.info("auth.login", user_id=user.id)
    return await _issue_token_pair(db, settings, user=user, user_agent=user_agent, ip=ip)


async def verify_login_2fa(
    db: AsyncSession,
    settings: Settings,
    *,
    pending_token: str,
    code: str,
    ip: str | None,
    user_agent: str | None,
) -> TokenPairResponse:
    """Exchange a ``2fa_pending`` token + TOTP/recovery code for real tokens."""
    claims = decode_token(settings=settings, token=pending_token)
    if claims is None or claims.get("type") != "2fa_pending":
        raise AppError("INVALID_TOKEN", "This 2FA challenge has expired. Sign in again.", status_code=401)

    try:
        user_id = int(claims["sub"])
    except (KeyError, ValueError):
        raise AppError("INVALID_TOKEN", "Invalid challenge.", status_code=401)

    if not await twofa_service.verify_code_or_recovery(db, user_id=user_id, code=code):
        raise AppError("INVALID_CODE", "That code didn't match. Try again.", status_code=400)

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.deleted_at is not None or user.status in ("suspended", "deactivated"):
        raise AppError("UNAUTHENTICATED", "This account is no longer active.", status_code=403)

    logger.info("auth.login_2fa_verified", user_id=user.id)
    return await _issue_token_pair(db, settings, user=user, user_agent=user_agent, ip=ip)


async def refresh(
    db: AsyncSession,
    settings: Settings,
    *,
    raw_refresh_token: str,
    ip: str | None,
    user_agent: str | None,
) -> TokenPairResponse:
    token_hash = _hash_opaque_token(raw_refresh_token)
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None:
        raise AppError("INVALID_TOKEN", "Invalid session. Sign in again.", status_code=401)

    now = datetime.now(timezone.utc)

    if row.revoked_at is not None:
        # Reuse of an already-rotated/revoked token: assume theft, kill the
        # whole family so every device on this login chain is signed out.
        await db.execute(
            RefreshToken.__table__.update()
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.flush()
        logger.warning("auth.refresh_reuse_detected", user_id=row.user_id, family_id=row.family_id)
        raise AppError(
            "TOKEN_REUSE_DETECTED",
            "This session was invalidated for security. Sign in again.",
            status_code=401,
        )

    if _naive(row.expires_at) < _naive(now):
        raise AppError("INVALID_TOKEN", "Invalid session. Sign in again.", status_code=401)

    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None or user.deleted_at is not None or user.status in ("suspended", "deactivated"):
        raise AppError("UNAUTHENTICATED", "This account is no longer active.", status_code=403)

    new_pair = await _issue_token_pair(
        db, settings, user=user, family_id=row.family_id, user_agent=user_agent, ip=ip
    )
    row.revoked_at = now
    new_hash = _hash_opaque_token(new_pair.refresh_token)
    new_row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == new_hash))
    ).scalar_one()
    row.replaced_by = new_row.id
    await db.flush()

    return new_pair


async def logout(db: AsyncSession, *, raw_refresh_token: str) -> int | None:
    """Revoke the given refresh token. Returns the owning user's id (for the
    router's audit-log call), or None if the token wasn't found/already dead.
    """
    token_hash = _hash_opaque_token(raw_refresh_token)
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        await db.flush()
        return row.user_id
    return None


async def forgot_password(db: AsyncSession, settings: Settings, *, email: str) -> None:
    """Always no-op silently for an unknown email — same response either way."""
    email_norm = email.lower()
    user = (
        await db.execute(select(User).where(User.email == email_norm))
    ).scalar_one_or_none()
    if user is None or user.deleted_at is not None or user.password_hash is None:
        return

    now = datetime.now(timezone.utc)
    recent = (
        await db.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id)
            .order_by(PasswordResetToken.created_at.desc())
        )
    ).scalars().first()
    if (
        recent is not None
        and recent.consumed_at is None
        and _naive(now) - _naive(recent.created_at) < RESEND_COOLDOWN
    ):
        return  # silently drop — still no signal to the caller either way

    raw = _new_opaque_token()
    db.add(
        PasswordResetToken(
            id=uuid.uuid4().hex,
            user_id=user.id,
            token_hash=_hash_opaque_token(raw),
            expires_at=now + PASSWORD_RESET_TTL,
        )
    )
    await db.flush()
    notifications.send_password_reset_email(to_email=user.email, token=raw)


async def reset_password(
    db: AsyncSession, *, raw_token: str, new_password: str
) -> int:
    """Returns the affected user's id (for the router's audit-log call)."""
    token_hash = _hash_opaque_token(raw_token)
    row = (
        await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.consumed_at is not None or _naive(row.expires_at) < _naive(now):
        raise AppError("INVALID_TOKEN", "This reset link is invalid or expired.", status_code=400)

    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one()
    user.password_hash = hash_password(new_password)
    # Invalidate every existing access token immediately...
    user.tokens_revoked_before = now
    row.consumed_at = now

    # This same token type/flow is reused for admin-invite acceptance (see
    # modules/users/service.py::invite_user): setting a password via a link
    # only the account owner could have received proves both that they
    # control the email AND that they're ready to use the account.
    if user.status == "invited":
        user.status = "active"
    if user.email_verified_at is None:
        user.email_verified_at = now

    # ...and every refresh token, so no device can silently mint a new
    # access token off an old session either.
    await db.execute(
        RefreshToken.__table__.update()
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.flush()
    logger.info("auth.password_reset", user_id=user.id)
    return user.id


# A real argon2 hash of a random value, used only so the "user not found"
# login path still calls verify_password (keeps timing shape consistent).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
