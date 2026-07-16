"""Shared FastAPI dependencies for the admin/user module."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.account import token_rejection
from app.core.responses import AppError
from app.core.security import decode_token
from app.db.base import get_sessionmaker
from app.db.models import User


async def get_db() -> AsyncIterator[AsyncSession]:
    """Per-request DB session.

    Commits on success AND on a deliberate :class:`AppError` (a controlled
    4xx business rejection — e.g. a failed-login audit row, or revoking a
    refresh-token family on reuse detection — is real work that must survive
    even though the request itself returns an error). Only rolls back on an
    *unexpected* exception, where the DB state mid-request can't be trusted.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except AppError:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Resolve the caller from a ``Authorization: Bearer <access token>`` header.

    Rejects: missing/malformed header, invalid/expired JWT, wrong token type
    (a refresh token can't authenticate a request), a token issued before the
    user's ``tokens_revoked_before`` watermark (force-logout), a soft-deleted
    user, or a suspended/deactivated account.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError("UNAUTHENTICATED", "Sign in required.", status_code=401)

    claims = decode_token(settings=settings, token=authorization[len("Bearer ") :])
    if claims is None or claims.get("type") != "access":
        raise AppError("UNAUTHENTICATED", "Invalid or expired session.", status_code=401)

    try:
        user_id = int(claims["sub"])
    except (KeyError, ValueError):
        raise AppError("UNAUTHENTICATED", "Invalid session.", status_code=401)

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()

    # Same rule the WebSocket applies — see app/core/account.py.
    rejection = token_rejection(user, issued_at=claims.get("iat", 0))
    if rejection == "UNAUTHENTICATED":
        raise AppError("UNAUTHENTICATED", "Invalid session.", status_code=401)
    if rejection == "SESSION_REVOKED":
        raise AppError(
            "UNAUTHENTICATED", "Session was revoked. Sign in again.", status_code=401
        )
    if rejection == "USER_SUSPENDED":
        raise AppError(
            "USER_SUSPENDED", "This account is no longer active.", status_code=403
        )

    assert user is not None  # token_rejection returns UNAUTHENTICATED for None
    return user


async def get_data_owner(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """The user whose notes/tasks a ``/notes``|``/tasks`` call may see and touch.

    A *present* Authorization header is held to the full :func:`get_current_user`
    standard — an expired or revoked token is a 401, never a quiet downgrade to
    anonymous, because a downgrade would answer with someone else's data instead
    of telling the app to refresh.

    A *missing* header resolves to the shared anonymous user, matching the WS
    endpoint: a local run with no login still reaches its own data. Where auth is
    enforced (:attr:`Settings.auth_enabled`) there is no such fallback — sign in
    or get nothing.
    """
    if authorization:
        return await get_current_user(
            authorization=authorization, db=db, settings=settings
        )
    if settings.auth_enabled:
        raise AppError("UNAUTHENTICATED", "Sign in required.", status_code=401)
    # Local import: app.db.repo imports app.db.models only, so no cycle — but
    # keeping it here preserves core/'s "depends on nothing above it" shape.
    from app.db import repo

    return await repo.get_or_create_user(db, repo.ANON_EXTERNAL_ID)


async def get_current_session_id(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str | None:
    """The ``fid`` (refresh-token family/session id) claim on the caller's
    access token, if present — used only by ``GET /me/sessions`` to mark
    which listed session is the caller's current one. Never raises: an
    absent/invalid token here just means "no current session to highlight",
    which is fine since :func:`get_current_user` already enforces auth on
    the same request.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    claims = decode_token(settings=settings, token=authorization[len("Bearer ") :])
    if claims is None:
        return None
    return claims.get("fid")


async def get_impersonator_id(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> int | None:
    """The ``act.impersonator_id`` claim on the caller's access token, if
    this is an impersonation session (see modules/impersonation/service.py).
    Never raises, for the same reason as :func:`get_current_session_id`.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    claims = decode_token(settings=settings, token=authorization[len("Bearer ") :])
    if claims is None:
        return None
    act = claims.get("act")
    return act.get("impersonator_id") if isinstance(act, dict) else None


def require_permission(code: str):
    """Dependency factory: ``Depends(require_permission("users.update"))``.

    Backend is the source of truth for every permission check — the frontend
    only *hides* UI for permissions the user lacks (see ``<Can>`` in
    docs/ADMIN_USER_MODULE_ARCHITECTURE.md); this dependency is what actually
    enforces it. A super_admin implicitly holds every code (seeded with all
    permissions in app/db/seed.py), so no special-case bypass is needed here.
    """

    async def _dependency(
        user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
    ) -> User:
        # Local import: app.modules.rbac.service -> app.db.models only, so this
        # doesn't create a cycle, but keeping it here (not top-level) makes the
        # core/ package's dependency direction explicit: core has no
        # compile-time dependency on any module/ package.
        from app.modules.rbac.service import get_user_permission_codes

        codes = await get_user_permission_codes(db, user.id)
        if code not in codes:
            raise AppError(
                "FORBIDDEN", f"Missing permission: {code}", status_code=403
            )
        return user

    return _dependency
