"""Session (device) listing and revocation.

A "session" here is a refresh-token family (see
app/modules/auth/service.py — every login mints a family, every refresh
rotates within it). A family is "active" while it has at least one
non-revoked, unexpired row; revoking a session means revoking every row in
that family.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import AppError
from app.db.models import RefreshToken, User
from app.modules.rbac import service as rbac_service


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


async def list_sessions(
    db: AsyncSession, *, user_id: int, current_family_id: str | None
) -> list[dict]:
    now = datetime.now(timezone.utc)
    active_rows = list(
        (
            await db.execute(
                select(RefreshToken)
                .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                .order_by(RefreshToken.last_used_at.desc())
            )
        ).scalars()
    )
    active_rows = [r for r in active_rows if _naive(r.expires_at) > _naive(now)]

    family_ids = [r.family_id for r in active_rows]
    first_seen: dict[str, datetime] = {}
    if family_ids:
        rows = await db.execute(
            select(RefreshToken.family_id, func.min(RefreshToken.issued_at))
            .where(RefreshToken.user_id == user_id, RefreshToken.family_id.in_(family_ids))
            .group_by(RefreshToken.family_id)
        )
        first_seen = dict(rows.all())

    return [
        {
            "family_id": r.family_id,
            "created_at": first_seen.get(r.family_id, r.issued_at),
            "last_used_at": r.last_used_at,
            "expires_at": r.expires_at,
            "user_agent": r.user_agent,
            "ip": r.ip,
            "is_current": r.family_id == current_family_id,
        }
        for r in active_rows
    ]


async def _revoke_family(db: AsyncSession, *, user_id: int, family_id: str) -> bool:
    """Revoke every non-revoked row in ``family_id``. Returns False if the
    family doesn't exist (or isn't the caller's) for the router to 404 on.
    """
    exists = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.family_id == family_id
            )
        )
    ).scalars().first()
    if exists is None:
        return False

    now = datetime.now(timezone.utc)
    await db.execute(
        RefreshToken.__table__.update()
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.flush()
    return True


async def revoke_own_session(db: AsyncSession, *, user_id: int, family_id: str) -> None:
    if not await _revoke_family(db, user_id=user_id, family_id=family_id):
        raise AppError("NOT_FOUND", "Session not found.", status_code=404)


async def revoke_other_sessions(
    db: AsyncSession, *, user_id: int, keep_family_id: str | None
) -> int:
    """"Log out other devices" — revoke every family except the caller's own."""
    now = datetime.now(timezone.utc)
    query = RefreshToken.__table__.update().where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    if keep_family_id:
        query = query.where(RefreshToken.family_id != keep_family_id)
    result = await db.execute(query.values(revoked_at=now))
    await db.flush()
    return result.rowcount or 0


async def admin_revoke_session(
    db: AsyncSession, *, actor: User, target_user_id: int, family_id: str
) -> None:
    """Admin-initiated revoke of another user's session.

    Same hierarchy guard as app/modules/users/service.py: an actor can only
    reach into a strictly-lower-level user's sessions unless they hold the
    super_admin (is_system) role.
    """
    if actor.id != target_user_id:
        actor_level, actor_is_system = await rbac_service.get_role_context(db, actor.id)
        target_level, _ = await rbac_service.get_role_context(db, target_user_id)
        if not actor_is_system and target_level >= actor_level:
            raise AppError(
                "INSUFFICIENT_ROLE_LEVEL",
                "You cannot manage sessions for a user with an equal or higher role.",
                status_code=403,
            )

    if not await _revoke_family(db, user_id=target_user_id, family_id=family_id):
        raise AppError("NOT_FOUND", "Session not found.", status_code=404)
