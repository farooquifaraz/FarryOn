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
)

router = APIRouter(prefix="/users", tags=["users"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _list_item(user: User, roles: list[str]) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "status": user.status,
        "email_verified": user.email_verified_at is not None,
        "roles": roles,
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
    page: int = 1,
    page_size: int = service.PAGE_SIZE_DEFAULT,
    db: AsyncSession = Depends(get_db),
) -> dict:
    items, total = await service.list_users(
        db,
        search=search,
        status_filter=status,
        role_filter=role,
        page=page,
        page_size=page_size,
    )
    return ok(
        [_list_item(u, roles) for u, roles in items],
        meta={"page": page, "page_size": page_size, "total": total},
    )


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
