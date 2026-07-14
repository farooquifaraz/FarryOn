"""``/api/v1/roles``, ``/api/v1/permissions``, ``/api/v1/users/{id}/roles``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_permission
from app.core.responses import ok
from app.db.models import User
from app.modules.audit.service import write_audit
from app.modules.rbac import service
from app.modules.rbac.schemas import (
    RoleCreateRequest,
    RoleOut,
    RoleUpdateRequest,
    SetUserRolesRequest,
)

router = APIRouter(tags=["rbac"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _role_out(role, codes: list[str]) -> RoleOut:
    return RoleOut(
        id=role.id,
        name=role.name,
        description=role.description,
        level=role.level,
        is_system=role.is_system,
        permissions=codes,
    )


@router.get("/permissions", dependencies=[Depends(require_permission("permissions.read"))])
async def list_permissions_endpoint(db: AsyncSession = Depends(get_db)) -> dict:
    perms = await service.list_permissions(db)
    return ok([{"code": p.code, "description": p.description} for p in perms])


@router.get("/roles", dependencies=[Depends(require_permission("permissions.read"))])
async def list_roles_endpoint(db: AsyncSession = Depends(get_db)) -> dict:
    roles = await service.list_roles(db)
    return ok([_role_out(role, codes).model_dump() for role, codes in roles])


@router.post("/roles", dependencies=[Depends(require_permission("roles.manage"))])
async def create_role_endpoint(
    body: RoleCreateRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    role = await service.create_role(
        db,
        name=body.name,
        description=body.description,
        level=body.level,
        permission_codes=body.permission_codes,
    )
    await write_audit(
        db,
        actor_id=actor.id,
        action="role.create",
        entity_type="role",
        entity_id=role.id,
        after=body.model_dump(),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(_role_out(role, body.permission_codes).model_dump())


@router.patch("/roles/{role_id}", dependencies=[Depends(require_permission("roles.manage"))])
async def update_role_endpoint(
    role_id: int,
    body: RoleUpdateRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    role = await service.update_role(
        db,
        role_id,
        description=body.description,
        level=body.level,
        permission_codes=body.permission_codes,
    )
    codes = await service.permission_codes_for_role(db, role.id)
    await write_audit(
        db,
        actor_id=actor.id,
        action="role.update",
        entity_type="role",
        entity_id=role.id,
        after=body.model_dump(exclude_none=True),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(_role_out(role, codes).model_dump())


@router.delete("/roles/{role_id}", dependencies=[Depends(require_permission("roles.manage"))])
async def delete_role_endpoint(
    role_id: int,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await service.delete_role(db, role_id)
    await write_audit(
        db,
        actor_id=actor.id,
        action="role.delete",
        entity_type="role",
        entity_id=role_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"deleted": True})


@router.get("/users/{user_id}/roles", dependencies=[Depends(require_permission("users.read"))])
async def get_user_roles_endpoint(user_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    roles = await service.get_user_roles(db, user_id)
    return ok([{"id": r.id, "name": r.name, "level": r.level} for r in roles])


@router.put("/users/{user_id}/roles", dependencies=[Depends(require_permission("users.update"))])
async def set_user_roles_endpoint(
    user_id: int,
    body: SetUserRolesRequest,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    roles = await service.set_user_roles(
        db, actor=actor, target_user_id=user_id, role_ids=body.role_ids
    )
    await write_audit(
        db,
        actor_id=actor.id,
        action="user.set_roles",
        entity_type="user",
        entity_id=user_id,
        after={"role_ids": body.role_ids, "roles": [r.name for r in roles]},
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok([{"id": r.id, "name": r.name, "level": r.level} for r in roles])
