"""Admin-side user management: list/search, invite, update, soft delete,
bulk actions, CSV export.

Guard rails mirror app/modules/rbac/service.py (same hierarchy the RBAC
guard rails protect — see docs/ADMIN_USER_MODULE_ARCHITECTURE.md):

- An admin can never act on their own account through this API (use
  ``/me/*`` instead) — prevents an accidental self-suspend/self-delete.
- An admin can only modify a user whose current highest role level is
  strictly lower than their own, unless the actor holds the (``is_system``)
  super_admin role.
- The last remaining super_admin can never be suspended or deleted.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.responses import AppError
from app.core.security import hash_opaque_token, new_opaque_token
from app.db.models import PasswordResetToken, RefreshToken, Role, User, UserRole
from app.modules.auth import notifications
from app.modules.auth.service import PASSWORD_RESET_TTL
from app.modules.rbac import service as rbac_service
from app.modules.users.schemas import BulkActionResultItem
from app.logging_conf import get_logger

logger = get_logger(__name__)

PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100


async def _assert_can_modify(db: AsyncSession, *, actor: User, target: User) -> None:
    if actor.id == target.id:
        raise AppError(
            "SELF_ACTION_FORBIDDEN",
            "Use /me to manage your own account.",
            status_code=403,
        )
    actor_level, actor_is_system = await rbac_service.get_role_context(db, actor.id)
    target_level, _ = await rbac_service.get_role_context(db, target.id)
    if not actor_is_system and target_level >= actor_level:
        raise AppError(
            "INSUFFICIENT_ROLE_LEVEL",
            "You cannot modify a user with an equal or higher role than your own.",
            status_code=403,
        )


async def role_names(db: AsyncSession, user_id: int) -> list[str]:
    roles = await rbac_service.get_user_roles(db, user_id)
    return [r.name for r in roles]


async def get_user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise AppError("NOT_FOUND", "User not found.", status_code=404)
    return user


async def list_users(
    db: AsyncSession,
    *,
    search: str | None,
    status_filter: str | None,
    role_filter: str | None,
    page: int,
    page_size: int,
) -> tuple[list[tuple[User, list[str]]], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)

    query = select(User).where(User.deleted_at.is_(None), User.email.is_not(None))
    count_query = select(func.count()).select_from(User).where(
        User.deleted_at.is_(None), User.email.is_not(None)
    )

    if search:
        like = f"%{search.lower()}%"
        cond = or_(func.lower(User.email).like(like), func.lower(User.display_name).like(like))
        query = query.where(cond)
        count_query = count_query.where(cond)

    if status_filter:
        query = query.where(User.status == status_filter)
        count_query = count_query.where(User.status == status_filter)

    if role_filter:
        query = query.join(UserRole, UserRole.user_id == User.id).join(
            Role, Role.id == UserRole.role_id
        ).where(Role.name == role_filter)
        count_query = count_query.join(UserRole, UserRole.user_id == User.id).join(
            Role, Role.id == UserRole.role_id
        ).where(Role.name == role_filter)

    total = (await db.execute(count_query)).scalar_one()
    rows = list(
        (
            await db.execute(
                query.order_by(User.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    items = [(u, await role_names(db, u.id)) for u in rows]
    return items, total


async def invite_user(
    db: AsyncSession,
    settings: Settings,
    *,
    actor: User,
    email: str,
    display_name: str | None,
    role_ids: list[int],
) -> User:
    email_norm = email.lower()
    existing = (
        await db.execute(select(User).where(User.email == email_norm))
    ).scalar_one_or_none()
    if existing is not None and existing.deleted_at is None:
        raise AppError(
            "EMAIL_TAKEN", "An account with this email already exists.",
            status_code=409, fields={"email": "already registered"},
        )

    roles = (
        list((await db.execute(select(Role).where(Role.id.in_(role_ids)))).scalars())
        if role_ids
        else []
    )
    if len(roles) != len(set(role_ids)):
        raise AppError("NOT_FOUND", "One or more role_ids do not exist.", status_code=404)

    actor_level, actor_is_system = await rbac_service.get_role_context(db, actor.id)
    if not actor_is_system and any(r.level >= actor_level for r in roles):
        raise AppError(
            "INSUFFICIENT_ROLE_LEVEL",
            "You cannot grant a role equal to or higher than your own.",
            status_code=403,
        )

    user = User(
        # Random, not email-derived — see the matching comment in
        # app/modules/auth/service.py::register for why.
        external_id=f"invited:{uuid.uuid4().hex}",
        email=email_norm,
        password_hash=None,
        display_name=display_name,
        status="invited",
    )
    db.add(user)
    await db.flush()
    for role in roles:
        db.add(UserRole(user_id=user.id, role_id=role.id))

    now = datetime.now(timezone.utc)
    raw = new_opaque_token()
    db.add(
        PasswordResetToken(
            id=uuid.uuid4().hex,
            user_id=user.id,
            token_hash=hash_opaque_token(raw),
            expires_at=now + PASSWORD_RESET_TTL,
        )
    )
    await db.flush()
    notifications.send_invite_email(to_email=user.email, token=raw)

    logger.info("users.invited", user_id=user.id, actor_id=actor.id)
    return user


async def update_user(
    db: AsyncSession,
    *,
    actor: User,
    user_id: int,
    display_name: str | None,
    status: str | None,
    timezone_: str | None,
    locale: str | None,
) -> User:
    target = await get_user_or_404(db, user_id)
    await _assert_can_modify(db, actor=actor, target=target)

    if status is not None and status != target.status:
        if status in ("suspended", "deactivated") and await rbac_service.is_last_super_admin(
            db, target.id
        ):
            raise AppError(
                "LAST_SUPER_ADMIN",
                "Cannot suspend or deactivate the last super_admin.",
                status_code=403,
            )
        target.status = status
        if status in ("suspended", "deactivated"):
            # Force-logout: any access token issued before now stops working,
            # and every refresh token is revoked (mirrors auth.reset_password).

            now = datetime.now(timezone.utc)
            target.tokens_revoked_before = now
            await db.execute(
                RefreshToken.__table__.update()
                .where(RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )

    if display_name is not None:
        target.display_name = display_name
    if timezone_ is not None:
        target.timezone = timezone_
    if locale is not None:
        target.locale = locale

    await db.flush()
    logger.info("users.updated", user_id=target.id, actor_id=actor.id)
    return target


async def soft_delete_user(db: AsyncSession, *, actor: User, user_id: int) -> None:
    target = await get_user_or_404(db, user_id)
    await _assert_can_modify(db, actor=actor, target=target)

    if await rbac_service.is_last_super_admin(db, target.id):
        raise AppError(
            "LAST_SUPER_ADMIN", "Cannot delete the last super_admin.", status_code=403
        )


    now = datetime.now(timezone.utc)
    target.deleted_at = now
    target.status = "deactivated"
    target.tokens_revoked_before = now
    await db.execute(
        RefreshToken.__table__.update()
        .where(RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(UserRole.__table__.delete().where(UserRole.user_id == target.id))
    await db.flush()
    logger.info("users.soft_deleted", user_id=target.id, actor_id=actor.id)


async def bulk_action(
    db: AsyncSession, *, actor: User, user_ids: list[int], action: str
) -> list[BulkActionResultItem]:
    results: list[BulkActionResultItem] = []
    for uid in user_ids:
        try:
            if action == "suspend":
                await update_user(
                    db, actor=actor, user_id=uid, display_name=None, status="suspended",
                    timezone_=None, locale=None,
                )
            elif action == "activate":
                await update_user(
                    db, actor=actor, user_id=uid, display_name=None, status="active",
                    timezone_=None, locale=None,
                )
            elif action == "delete":
                await soft_delete_user(db, actor=actor, user_id=uid)
            results.append(BulkActionResultItem(user_id=uid, ok=True))
        except AppError as exc:
            # One bad row shouldn't sink the whole batch — report per-item and
            # keep going, same spirit as the endpoint's idempotency intent.
            results.append(BulkActionResultItem(user_id=uid, ok=False, error=exc.code))
    return results


async def export_csv(db: AsyncSession) -> str:
    rows = list(
        (
            await db.execute(
                select(User)
                .where(User.deleted_at.is_(None), User.email.is_not(None))
                .order_by(User.created_at.desc())
            )
        ).scalars()
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "email", "display_name", "status", "roles", "created_at"])
    for u in rows:
        roles = ",".join(await role_names(db, u.id))
        writer.writerow([u.id, u.email, u.display_name or "", u.status, roles, u.created_at.isoformat()])
    return buf.getvalue()
