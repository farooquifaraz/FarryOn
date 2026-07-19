"""Seed data for the admin/user module: default permissions, roles, and the
first super_admin account.

Idempotent — safe to run on every deploy. Roles/permissions are upserted by
name/code; the first super_admin is only created (or promoted, if the email
already exists as a plain user) when ``FIRST_SUPER_ADMIN_EMAIL`` and
``FIRST_SUPER_ADMIN_PASSWORD`` are set in the environment.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.security import hash_password
from app.db.models import Permission, Plan, Role, RolePermission, User, UserRole
from app.logging_conf import get_logger

logger = get_logger(__name__)

# The billing catalog. Prices live here (the plans table is billing's source of
# truth); the matching daily caps live in Settings.plan_limits, keyed by the
# same name — one place for money, one for cost-control, joined by name. The
# "free" fallback tier is not sold, so it is not seeded here.
#
# name -> (price_cents, interval, description)
PLAN_CATALOG: dict[str, tuple[int, str, str]] = {
    "plus": (999, "month", "Plus — 7 minutes of voice a day, 20 scans."),
    "pro": (1999, "month", "Pro — 15 minutes of voice a day, unlimited scans."),
}

# code -> human description
DEFAULT_PERMISSIONS: dict[str, str] = {
    "users.read": "View user accounts and profiles",
    "users.create": "Create/invite new user accounts",
    "users.update": "Edit user accounts, status, and role assignments",
    "users.delete": "Soft-delete user accounts",
    "users.impersonate": "Sign in as another user (\"login as\")",
    "roles.manage": "Create, edit, and delete custom roles",
    "permissions.read": "View the permission catalog",
    "audit.read": "View and export the audit log",
    "sessions.manage": "View and revoke any user's active sessions",
    "settings.manage": "Edit system settings and feature flags",
    "content.moderate": "View and moderate notes/tasks/conversations/transcripts",
    "billing.read": "View subscriptions and revenue",
    "billing.manage": "Edit plans and issue refunds",
    "dashboard.read": "View the admin usage/analytics dashboard",
}

# role name -> (level, is_system, description, permission codes | "*" for all)
DEFAULT_ROLES: dict[str, tuple[int, bool, str, list[str] | str]] = {
    "super_admin": (100, True, "Full, unrestricted access.", "*"),
    "admin": (80, False, "Operational admin — everything except role management.", [
        c for c in DEFAULT_PERMISSIONS if c != "roles.manage"
    ]),
    "manager": (50, False, "Read + moderate, no destructive user actions.", [
        "users.read", "audit.read", "content.moderate", "billing.read",
        "dashboard.read", "permissions.read",
    ]),
    "user": (10, False, "Regular end user — no admin-module permissions.", []),
}


async def seed_roles_and_permissions(session: AsyncSession) -> dict[str, Role]:
    """Upsert :data:`DEFAULT_PERMISSIONS` and :data:`DEFAULT_ROLES`.

    Returns a ``{role_name: Role}`` map for convenience (e.g. assigning the
    first super_admin their role in the same run).
    """
    existing_perms = {
        p.code: p
        for p in (await session.execute(select(Permission))).scalars()
    }
    for code, description in DEFAULT_PERMISSIONS.items():
        if code not in existing_perms:
            perm = Permission(code=code, description=description)
            session.add(perm)
            existing_perms[code] = perm
    await session.flush()

    existing_roles = {
        r.name: r for r in (await session.execute(select(Role))).scalars()
    }
    for name, (level, is_system, description, perm_codes) in DEFAULT_ROLES.items():
        role = existing_roles.get(name)
        if role is None:
            role = Role(
                name=name, level=level, is_system=is_system, description=description
            )
            session.add(role)
            existing_roles[name] = role
        else:
            role.level = level
            role.description = description
        await session.flush()

        codes = (
            list(existing_perms.keys()) if perm_codes == "*" else list(perm_codes)
        )
        current = {
            rp.permission_id
            for rp in (
                await session.execute(
                    select(RolePermission).where(RolePermission.role_id == role.id)
                )
            ).scalars()
        }
        for code in codes:
            perm = existing_perms[code]
            if perm.id not in current:
                session.add(RolePermission(role_id=role.id, permission_id=perm.id))

    await session.flush()
    logger.info(
        "seed.roles_and_permissions",
        roles=list(existing_roles.keys()),
        permissions=len(existing_perms),
    )
    return existing_roles


async def seed_first_super_admin(
    session: AsyncSession, settings: Settings, roles: dict[str, Role]
) -> None:
    """Create or promote the env-configured first super_admin account.

    No-op unless both ``first_super_admin_email`` and
    ``first_super_admin_password`` are set. Safe to leave set across
    redeploys — an existing matching user is only (re-)granted the
    super_admin role, its password is never overwritten.
    """
    email = settings.first_super_admin_email
    password = settings.first_super_admin_password
    if not email or not password:
        logger.info("seed.super_admin.skipped", reason="env vars not set")
        return

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            external_id=f"admin-seed:{email}",
            email=email,
            password_hash=hash_password(password),
            display_name="Super Admin",
            status="active",
        )
        session.add(user)
        await session.flush()
        logger.info("seed.super_admin.created", email=email)
    else:
        logger.info("seed.super_admin.promoted_existing", email=email)

    super_admin_role = roles["super_admin"]
    already_has_role = await session.execute(
        select(UserRole).where(
            UserRole.user_id == user.id, UserRole.role_id == super_admin_role.id
        )
    )
    if already_has_role.scalar_one_or_none() is None:
        session.add(UserRole(user_id=user.id, role_id=super_admin_role.id))
        await session.flush()


async def seed_plans(session: AsyncSession) -> None:
    """Upsert the billing catalog. Idempotent — safe on every deploy.

    Matched by name: a plan that exists has its price/interval/description
    brought in line with the catalog (so a price change here reaches the DB on
    the next deploy), and a missing one is created active. Plans NOT in the
    catalog are left untouched — an operator may have added a custom plan
    through the admin panel, and this must not delete it. `is_active` is only
    set on create, so an operator can retire a catalog plan by deactivating it
    without this resurrecting it every deploy.
    """
    for name, (price_cents, interval, description) in PLAN_CATALOG.items():
        existing = (
            await session.execute(select(Plan).where(Plan.name == name))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Plan(
                    name=name,
                    price_cents=price_cents,
                    currency="USD",
                    interval=interval,
                    description=description,
                    is_active=True,
                )
            )
        else:
            existing.price_cents = price_cents
            existing.interval = interval
            existing.description = description
    await session.flush()


async def run_seed(session: AsyncSession, settings: Settings) -> None:
    """Run the full seed sequence (roles/permissions, plans, first super_admin)."""
    roles = await seed_roles_and_permissions(session)
    await seed_plans(session)
    await seed_first_super_admin(session, settings, roles)
