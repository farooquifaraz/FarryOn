"""RBAC engine: role/permission CRUD, permission-lookup, and the guard rails
that protect the admin hierarchy itself (see docs/ADMIN_USER_MODULE_ARCHITECTURE.md).

Guard rails enforced by :func:`set_user_roles`:

- A user can never change their own roles (self-role-edit block) — even a
  super_admin must have another admin do it, preventing an accidental
  self-lockout from going unnoticed.
- A non-super_admin actor cannot modify a user whose current highest role
  level is >= the actor's own highest level — an ``admin`` can't touch
  another ``admin`` or anyone above, only edits strictly-lower-level users.
  ``super_admin`` (the one ``is_system`` role) bypasses this check.
- The last remaining ``super_admin`` can never be demoted — there must
  always be at least one account holding it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import AppError
from app.db.models import Permission, Role, RolePermission, User, UserRole

SUPER_ADMIN_ROLE_NAME = "super_admin"


async def list_permissions(db: AsyncSession) -> list[Permission]:
    return list((await db.execute(select(Permission).order_by(Permission.code))).scalars())


async def permission_codes_for_role(db: AsyncSession, role_id: int) -> list[str]:
    rows = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(Permission.code)
    )
    return list(rows.scalars())


async def list_roles(db: AsyncSession) -> list[tuple[Role, list[str]]]:
    roles = list((await db.execute(select(Role).order_by(Role.level.desc()))).scalars())
    return [(role, await permission_codes_for_role(db, role.id)) for role in roles]


async def get_role(db: AsyncSession, role_id: int) -> Role:
    role = await db.get(Role, role_id)
    if role is None:
        raise AppError("NOT_FOUND", "Role not found.", status_code=404)
    return role


async def _resolve_permissions(db: AsyncSession, codes: list[str]) -> list[Permission]:
    if not codes:
        return []
    perms = list(
        (await db.execute(select(Permission).where(Permission.code.in_(codes)))).scalars()
    )
    found = {p.code for p in perms}
    missing = set(codes) - found
    if missing:
        raise AppError(
            "UNKNOWN_PERMISSION", f"Unknown permission code(s): {', '.join(sorted(missing))}",
            status_code=400,
        )
    return perms


async def create_role(
    db: AsyncSession, *, name: str, description: str | None, level: int, permission_codes: list[str]
) -> Role:
    existing = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if existing is not None:
        raise AppError("ROLE_EXISTS", "A role with this name already exists.", status_code=409)

    role = Role(name=name, description=description, level=level, is_system=False)
    db.add(role)
    await db.flush()

    for perm in await _resolve_permissions(db, permission_codes):
        db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    await db.flush()
    return role


async def update_role(
    db: AsyncSession,
    role_id: int,
    *,
    description: str | None,
    level: int | None,
    permission_codes: list[str] | None,
) -> Role:
    role = await get_role(db, role_id)
    if role.is_system:
        raise AppError(
            "SYSTEM_ROLE_LOCKED", "The super_admin role cannot be edited.", status_code=403
        )

    if description is not None:
        role.description = description
    if level is not None:
        role.level = level

    if permission_codes is not None:
        await db.execute(
            RolePermission.__table__.delete().where(RolePermission.role_id == role.id)
        )
        for perm in await _resolve_permissions(db, permission_codes):
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))

    await db.flush()
    return role


async def delete_role(db: AsyncSession, role_id: int) -> None:
    role = await get_role(db, role_id)
    if role.is_system:
        raise AppError(
            "SYSTEM_ROLE_LOCKED", "The super_admin role cannot be deleted.", status_code=403
        )
    in_use = (
        await db.execute(select(UserRole).where(UserRole.role_id == role.id))
    ).scalar_one_or_none()
    if in_use is not None:
        raise AppError(
            "ROLE_IN_USE", "This role is still assigned to one or more users.", status_code=409
        )
    await db.execute(RolePermission.__table__.delete().where(RolePermission.role_id == role.id))
    await db.delete(role)
    await db.flush()


async def get_user_roles(db: AsyncSession, user_id: int) -> list[Role]:
    return list(
        (
            await db.execute(
                select(Role).join(UserRole, UserRole.role_id == Role.id).where(
                    UserRole.user_id == user_id
                )
            )
        ).scalars()
    )


async def get_user_permission_codes(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    )
    return set(rows.scalars())


async def _max_role_level(roles: list[Role]) -> int:
    return max((r.level for r in roles), default=0)


async def is_last_super_admin(db: AsyncSession, user_id: int) -> bool:
    """True if ``user_id`` holds super_admin and no other account does.

    Shared guard used both when removing the role directly
    (:func:`set_user_roles`) and when suspending/deleting the account
    entirely (``modules/users/service.py``) — either action would otherwise
    leave the system with zero super_admins.
    """
    super_admin_role = (
        await db.execute(select(Role).where(Role.name == SUPER_ADMIN_ROLE_NAME))
    ).scalar_one_or_none()
    if super_admin_role is None:
        return False
    holds_it = (
        await db.execute(
            select(UserRole).where(
                UserRole.user_id == user_id, UserRole.role_id == super_admin_role.id
            )
        )
    ).scalar_one_or_none()
    if holds_it is None:
        return False
    other = (
        await db.execute(
            select(UserRole).where(
                UserRole.role_id == super_admin_role.id, UserRole.user_id != user_id
            )
        )
    ).scalars().first()
    return other is None


async def get_role_context(db: AsyncSession, user_id: int) -> tuple[int, bool]:
    """``(max_role_level, holds_a_system_role)`` for ``user_id``.

    Shared by any module enforcing the same hierarchy guard rail as
    :func:`set_user_roles` (e.g. ``modules/users`` — an admin editing/
    suspending/deleting another user must pass the same "strictly lower
    level, unless I'm super_admin" check as editing their roles does).
    """
    roles = await get_user_roles(db, user_id)
    return await _max_role_level(roles), any(r.is_system for r in roles)


async def set_user_roles(
    db: AsyncSession, *, actor: User, target_user_id: int, role_ids: list[int]
) -> list[Role]:
    if actor.id == target_user_id:
        raise AppError(
            "SELF_ROLE_EDIT_FORBIDDEN", "You cannot change your own roles.", status_code=403
        )

    target = await db.get(User, target_user_id)
    if target is None or target.deleted_at is not None:
        raise AppError("NOT_FOUND", "User not found.", status_code=404)

    actor_roles = await get_user_roles(db, actor.id)
    actor_is_super = any(r.is_system for r in actor_roles)
    actor_level = await _max_role_level(actor_roles)

    target_current_roles = await get_user_roles(db, target_user_id)
    target_current_level = await _max_role_level(target_current_roles)

    if not actor_is_super and target_current_level >= actor_level:
        raise AppError(
            "INSUFFICIENT_ROLE_LEVEL",
            "You cannot modify a user with an equal or higher role than your own.",
            status_code=403,
        )

    new_roles = (
        list((await db.execute(select(Role).where(Role.id.in_(role_ids)))).scalars())
        if role_ids
        else []
    )
    if len(new_roles) != len(set(role_ids)):
        raise AppError("NOT_FOUND", "One or more role_ids do not exist.", status_code=404)

    super_admin_role = (
        await db.execute(select(Role).where(Role.name == SUPER_ADMIN_ROLE_NAME))
    ).scalar_one_or_none()
    if super_admin_role is not None:
        currently_super = any(r.id == super_admin_role.id for r in target_current_roles)
        will_be_super = any(r.id == super_admin_role.id for r in new_roles)
        if currently_super and not will_be_super:
            other_supers = (
                await db.execute(
                    select(UserRole).where(
                        UserRole.role_id == super_admin_role.id,
                        UserRole.user_id != target_user_id,
                    )
                )
            ).scalars().first()
            if other_supers is None:
                raise AppError(
                    "LAST_SUPER_ADMIN",
                    "Cannot remove the last super_admin.",
                    status_code=403,
                )

    await db.execute(UserRole.__table__.delete().where(UserRole.user_id == target_user_id))
    for role in new_roles:
        db.add(UserRole(user_id=target_user_id, role_id=role.id))
    await db.flush()
    return new_roles
