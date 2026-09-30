"""``/api/v1/users*`` — admin-side user management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.deps import get_current_user, get_db, require_permission
from app.core.responses import ok
from app.db.models import User
from app.modules.audit.service import write_audit
from app.modules.users import service
from app.modules.users.schemas import (
    BulkActionRequest,
    InviteUserRequest,
    UpdateUserRequest,
    UpgradeEmailBulkRequest,
    UpgradeEmailRequest,
)

router = APIRouter(prefix="/users", tags=["users"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


STAFF_ROLES = ("super_admin", "admin", "manager")


def _list_item(user: User, roles: list[str]) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "status": user.status,
        "email_verified": user.email_verified_at is not None,
        "roles": roles,
        # staff = anyone with an admin-module role; everyone else is an app user
        "is_staff": any(r in STAFF_ROLES for r in roles),
        "country": user.country,
        "timezone": user.timezone,
        "created_at": user.created_at.isoformat(),
    }


def _detail(user: User, roles: list[str]) -> dict:
    return {
        **_list_item(user, roles),
        "timezone": user.timezone,
        "locale": user.locale,
        "avatar_url": user.avatar_url,
        "updated_at": user.updated_at.isoformat(),
    }


@router.get("", dependencies=[Depends(require_permission("users.read"))])
async def list_users_endpoint(
    search: str | None = None,
    status: str | None = None,
    role: str | None = None,
    kind: str | None = None,
    quota: str | None = None,
    app: str | None = None,
    page: int = 1,
    page_size: int = service.PAGE_SIZE_DEFAULT,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """``kind=app`` — people who signed up in the app (no admin role);
    ``kind=staff`` — anyone with one. Omitted: everyone.

    Every row carries the person's talk budget, app build and nudge state
    (billing/nudge.py). ``quota=out|near`` and ``app=outdated`` filter on
    those — computed, so the page is cut after the filter, not before.
    """
    from app.modules.billing.nudge import latest_app_build, user_insight

    filtered = quota in ("out", "near") or app == "outdated"
    items, total = await service.list_users(
        db,
        search=search,
        status_filter=status,
        role_filter=role,
        kind=kind,
        page=1 if filtered else page,
        page_size=service.PAGE_SIZE_MAX if filtered else page_size,
    )
    latest = latest_app_build()
    rows = []
    for u, roles in items:
        insight = await user_insight(db, u, latest_build=latest)
        if quota and insight["quota"]["state"] != quota:
            continue
        if app == "outdated" and not insight["app"]["outdated"]:
            continue
        rows.append({**_list_item(u, roles), **insight})
    if filtered:
        total = len(rows)
        start = (max(page, 1) - 1) * page_size
        rows = rows[start : start + page_size]
    return ok(rows, meta={"page": page, "page_size": page_size, "total": total})


@router.get(
    "/export", dependencies=[Depends(require_permission("users.read"))]
)
async def export_users_endpoint(db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    csv_text = await service.export_csv(db)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"},
    )


@router.get(
    "/usage", dependencies=[Depends(require_permission("users.read"))]
)
async def users_usage_endpoint(
    month: str | None = None,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Who used what, per user, for one month — every metered feature.

    Declared BEFORE ``/{user_id}`` on purpose: a literal path must win over the
    parameterised one, or "usage" is read as a user id and 422s.
    """
    return ok(
        await service.usage_report(
            db, month=month, page=page, page_size=page_size, search=q
        )
    )


@router.get(
    "/summary", dependencies=[Depends(require_permission("users.read"))]
)
async def users_summary_endpoint(db: AsyncSession = Depends(get_db)) -> dict:
    """The dashboard's counts over app users: how many have used up their
    talk time without upgrading, how many are near it, how many run an old
    build — and what the latest build is. Declared before ``/{user_id}``
    like ``/usage``."""
    from app.modules.billing.nudge import latest_app_build, user_insight

    settings = get_settings()
    items, total = await service.list_users(
        db, search=None, status_filter=None, role_filter=None, kind="app",
        page=1, page_size=service.PAGE_SIZE_MAX,
    )
    latest = latest_app_build()
    out = near = outdated = paying = 0
    for u, _roles in items:
        insight = await user_insight(db, u, latest_build=latest)
        state = insight["quota"]["state"]
        out += state == "out"
        near += state == "near"
        outdated += insight["app"]["outdated"]
        plan = insight["quota"]["plan"]
        paying += plan != settings.default_plan and not settings.is_trial_plan(plan)
    return ok({
        "app_users": total,
        "paying": paying,
        "out_of_quota": out,
        "near_limit": near,
        "outdated_app": outdated,
        "latest_build": latest,
    })


@router.post(
    "/upgrade-email",
    dependencies=[Depends(require_permission("billing.manage"))],
)
async def upgrade_email_bulk_endpoint(
    body: UpgradeEmailBulkRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send the upgrade email to several people: the ids given, or everyone
    who has used up their talk time. Each send follows the nudge rules (a
    verified address, no opt-out, the 7-day gap), so the answer says who
    was skipped and why."""
    from app.modules.billing.nudge import (
        latest_app_build,
        send_upgrade_nudge,
        user_insight,
    )

    targets: list[User] = []
    if body.all_out_of_quota:
        items, _ = await service.list_users(
            db, search=None, status_filter=None, role_filter=None, kind="app",
            page=1, page_size=service.PAGE_SIZE_MAX,
        )
        latest = latest_app_build()
        for u, _roles in items:
            if (await user_insight(db, u, latest_build=latest))["quota"]["state"] == "out":
                targets.append(u)
    for uid in body.ids or []:
        targets.append(await service.get_user_or_404(db, uid))

    sent: list[int] = []
    skipped: list[dict] = []
    seen: set[int] = set()
    for u in targets:
        if u.id in seen:
            continue
        seen.add(u.id)
        result = await send_upgrade_nudge(db, user=u, source="admin")
        if result == "sent":
            sent.append(u.id)
        else:
            skipped.append({"id": u.id, "reason": result.split(":", 1)[1]})
    if sent:
        await write_audit(
            db,
            actor_id=actor.id,
            action="billing.upgrade_email",
            entity_type="user",
            entity_id=sent[0] if len(sent) == 1 else None,
            after={"sent": sent, "skipped": skipped},
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    await db.commit()
    return ok({"sent": sent, "skipped": skipped})


@router.post(
    "/{user_id}/upgrade-email",
    dependencies=[Depends(require_permission("billing.manage"))],
)
async def upgrade_email_endpoint(
    user_id: int,
    request: Request,
    body: UpgradeEmailRequest | None = None,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send one person the upgrade email now (subject to the nudge rules;
    ``force`` waives the weekly gap only)."""
    from app.modules.billing.nudge import send_upgrade_nudge

    user = await service.get_user_or_404(db, user_id)
    force = bool(body and body.force)
    result = await send_upgrade_nudge(db, user=user, source="admin", ignore_gap=force)
    if result == "sent":
        await write_audit(
            db,
            actor_id=actor.id,
            action="billing.upgrade_email",
            entity_type="user",
            entity_id=user.id,
            after={"sent": [user.id], "forced": force},
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    await db.commit()
    return ok({
        "result": result.split(":", 1)[0],
        "reason": result.split(":", 1)[1] if ":" in result else None,
        "nudged_at": user.upgrade_nudged_at.isoformat() if user.upgrade_nudged_at else None,
    })


@router.get("/{user_id}", dependencies=[Depends(require_permission("users.read"))])
async def get_user_endpoint(user_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    user = await service.get_user_or_404(db, user_id)
    roles = await service.role_names(db, user.id)
    return ok(_detail(user, roles))


@router.post("", dependencies=[Depends(require_permission("users.create"))])
async def invite_user_endpoint(
    body: InviteUserRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    user = await service.invite_user(
        db,
        settings,
        actor=actor,
        email=body.email,
        display_name=body.display_name,
        role_ids=body.role_ids,
    )
    roles = await service.role_names(db, user.id)
    await write_audit(
        db,
        actor_id=actor.id,
        action="user.invite",
        entity_type="user",
        entity_id=user.id,
        after={"email": user.email, "role_ids": body.role_ids},
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(_detail(user, roles))


@router.patch("/{user_id}", dependencies=[Depends(require_permission("users.update"))])
async def update_user_endpoint(
    user_id: int,
    body: UpdateUserRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await service.update_user(
        db,
        actor=actor,
        user_id=user_id,
        display_name=body.display_name,
        status=body.status,
        timezone_=body.timezone,
        locale=body.locale,
    )
    roles = await service.role_names(db, user.id)
    await write_audit(
        db,
        actor_id=actor.id,
        action="user.update",
        entity_type="user",
        entity_id=user.id,
        after=body.model_dump(exclude_none=True),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(_detail(user, roles))


@router.delete("/{user_id}", dependencies=[Depends(require_permission("users.delete"))])
async def delete_user_endpoint(
    user_id: int,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await service.soft_delete_user(db, actor=actor, user_id=user_id)
    await write_audit(
        db,
        actor_id=actor.id,
        action="user.delete",
        entity_type="user",
        entity_id=user_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"deleted": True})


@router.post("/bulk", dependencies=[Depends(require_permission("users.update"))])
async def bulk_action_endpoint(
    body: BulkActionRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    results = await service.bulk_action(
        db, actor=actor, user_ids=body.user_ids, action=body.action
    )
    await write_audit(
        db,
        actor_id=actor.id,
        action=f"user.bulk_{body.action}",
        entity_type="user",
        after={"results": [r.model_dump() for r in results]},
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok([r.model_dump() for r in results])
