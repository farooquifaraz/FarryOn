"""TOTP enrollment/verification, recovery codes, admin force-disable.

Security notes:

- The TOTP secret is stored plaintext (see TotpSecret's docstring in
  app/db/models.py for the documented trade-off).
- Recovery codes are random opaque tokens, hashed at rest the same way as
  refresh/reset tokens (see core/security.py::hash_opaque_token) — fast hash
  is correct since these are high-entropy random values, not passwords.
- Enrollment is two-step: :func:`enroll` creates a *pending* secret (nothing
  is trusted yet); :func:`confirm_enroll` only flips it to enabled after the
  caller proves they can generate a valid code from it. This prevents a
  user "enabling" 2FA on a secret they never actually saved/scanned.
"""

from __future__ import annotations

import base64
import io
import secrets
from datetime import datetime, timezone

import pyotp
import qrcode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import AppError
from app.core.security import hash_opaque_token, verify_password
from app.db.models import RecoveryCode, TotpSecret, User
from app.modules.rbac import service as rbac_service
from app.logging_conf import get_logger

logger = get_logger(__name__)

RECOVERY_CODE_COUNT = 10
ISSUER = "FarryOn"


def _make_qr_png_base64(uri: str) -> str:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def is_enabled(db: AsyncSession, user_id: int) -> bool:
    row = (
        await db.execute(select(TotpSecret).where(TotpSecret.user_id == user_id))
    ).scalar_one_or_none()
    return row is not None and row.enabled_at is not None


async def enroll(db: AsyncSession, *, user: User) -> tuple[str, str, str]:
    """Start (or restart) enrollment. Returns (secret, otpauth_uri, qr_png_b64)."""
    existing = (
        await db.execute(select(TotpSecret).where(TotpSecret.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None and existing.enabled_at is not None:
        raise AppError(
            "TWOFA_ALREADY_ENABLED", "2FA is already enabled on this account.",
            status_code=409,
        )

    secret = pyotp.random_base32()
    if existing is not None:
        existing.secret = secret
        existing.created_at = datetime.now(timezone.utc)
    else:
        db.add(TotpSecret(user_id=user.id, secret=secret))
    await db.flush()

    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return secret, uri, _make_qr_png_base64(uri)


def _generate_recovery_codes() -> list[str]:
    return [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(RECOVERY_CODE_COUNT)]


async def confirm_enroll(db: AsyncSession, *, user: User, code: str) -> list[str]:
    row = (
        await db.execute(select(TotpSecret).where(TotpSecret.user_id == user.id))
    ).scalar_one_or_none()
    if row is None or row.enabled_at is not None:
        raise AppError(
            "TWOFA_NOT_PENDING", "No pending 2FA enrollment to confirm.", status_code=400
        )
    if not pyotp.TOTP(row.secret).verify(code, valid_window=1):
        raise AppError("INVALID_CODE", "That code didn't match. Try again.", status_code=400)

    row.enabled_at = datetime.now(timezone.utc)
    raw_codes = _generate_recovery_codes()
    for raw in raw_codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_opaque_token(raw)))
    await db.flush()
    logger.info("twofa.enabled", user_id=user.id)
    return raw_codes


async def verify_code_or_recovery(db: AsyncSession, *, user_id: int, code: str) -> bool:
    """True (and, for a recovery code, marks it consumed) iff ``code`` is a
    valid live TOTP code OR an unused recovery code for this user."""
    row = (
        await db.execute(select(TotpSecret).where(TotpSecret.user_id == user_id))
    ).scalar_one_or_none()
    if row is not None and row.enabled_at is not None:
        if pyotp.TOTP(row.secret).verify(code, valid_window=1):
            return True

    code_hash = hash_opaque_token(code)
    recovery = (
        await db.execute(
            select(RecoveryCode).where(
                RecoveryCode.user_id == user_id,
                RecoveryCode.code_hash == code_hash,
                RecoveryCode.used_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if recovery is not None:
        recovery.used_at = datetime.now(timezone.utc)
        await db.flush()
        logger.info("twofa.recovery_code_used", user_id=user_id)
        return True

    return False


async def disable(db: AsyncSession, *, user: User, password: str) -> None:
    if user.password_hash is None or not verify_password(password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Incorrect password.", status_code=401)
    await _clear_2fa(db, user.id)
    logger.info("twofa.disabled", user_id=user.id)


async def admin_force_disable(db: AsyncSession, *, actor: User, target_user_id: int) -> None:
    if actor.id != target_user_id:
        actor_level, actor_is_system = await rbac_service.get_role_context(db, actor.id)
        target_level, _ = await rbac_service.get_role_context(db, target_user_id)
        if not actor_is_system and target_level >= actor_level:
            raise AppError(
                "INSUFFICIENT_ROLE_LEVEL",
                "You cannot manage 2FA for a user with an equal or higher role.",
                status_code=403,
            )
    await _clear_2fa(db, target_user_id)
    logger.info("twofa.admin_force_disabled", user_id=target_user_id, actor_id=actor.id)


async def _clear_2fa(db: AsyncSession, user_id: int) -> None:
    await db.execute(TotpSecret.__table__.delete().where(TotpSecret.user_id == user_id))
    await db.execute(RecoveryCode.__table__.delete().where(RecoveryCode.user_id == user_id))
    await db.flush()
