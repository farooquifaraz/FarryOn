"""Password hashing and JWT helpers shared by the admin/user module.

Password hashing uses argon2id (via ``argon2-cffi``) per the module's security
requirements. JWT encode/decode uses :data:`Settings.jwt_secret` — the same
secret already used for the ``/ws/live`` handshake token (see
``app/config.py``), so there is exactly one signing key for the whole service.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

from app.config import Settings

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh", "2fa_pending"]


def new_opaque_token() -> str:
    """A high-entropy random token for refresh/verification/reset/invite links."""
    return secrets.token_urlsafe(32)


def hash_opaque_token(raw: str) -> str:
    """SHA-256 hex digest of an opaque token, for at-rest storage.

    Deliberately NOT argon2 — these are already-random high-entropy values
    (not user-chosen secrets), so a fast hash is correct; it avoids paying
    argon2's ~100ms cost on every token lookup while still meaning a stolen
    DB dump can't be replayed as a live token without inverting SHA-256.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_password(raw_password: str) -> str:
    """Hash a plaintext password with argon2id."""
    return _hasher.hash(raw_password)


def verify_password(raw_password: str, password_hash: str) -> bool:
    """Constant-time verify; never raises on a bad password (returns False)."""
    try:
        return _hasher.verify(password_hash, raw_password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash was made with weaker-than-current parameters."""
    return _hasher.check_needs_rehash(password_hash)


def encode_token(
    *,
    settings: Settings,
    user_id: int,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Encode a signed JWT for the admin/user module.

    ``sub`` is the user id (as a string, per JWT convention); ``type``
    distinguishes access vs. refresh tokens so one can't be replayed as the
    other. ``iat`` is used by :func:`is_token_revoked`-style checks against
    ``User.tokens_revoked_before``.
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(*, settings: Settings, token: str) -> dict[str, Any] | None:
    """Decode+verify a JWT, returning its claims or ``None`` if invalid/expired."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
