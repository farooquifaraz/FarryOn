"""``/api/v1/me/sessions`` (self-service) and ``/api/v1/users/{id}/sessions``
(admin) — list and revoke active login sessions (refresh-token families).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_session_id, get_current_user, get_db, require_permission
from app.core.responses import ok
from app.db.models import User
from app.modules.audit.service import write_audit
from app.modules.sessions import service
from app.modules.sessions.schemas import SessionOut

me_router = APIRouter(prefix="/me/sessions", tags=["sessions"])
admin_router = APIRouter(prefix="/users/{user_id}/sessions", tags=["sessions"])


def _out(row: dict) -> dict:
    return SessionOut(**row).model_dump()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@me_router.get("")
async def list_my_sessions(
    user: User = Depends(get_current_user),
    current_family_id: str | None = Depends(get_current_session_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = await service.list_sessions(
        db, user_id=user.id, current_family_id=current_family_id
    )
    return ok([_out(r) for r in rows])


@me_router.delete("/{family_id}")
async def revoke_my_session(
    family_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await service.revoke_own_session(db, user_id=user.id, family_id=family_id)
    await write_audit(
        db,
        actor_id=user.id,
        action="session.revoke",
        entity_type="session",
        entity_id=family_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"revoked": True})


@me_router.post("/revoke-others")
async def revoke_other_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    current_family_id: str | None = Depends(get_current_session_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    count = await service.revoke_other_sessions(
        db, user_id=user.id, keep_family_id=current_family_id
    )
    await write_audit(
        db,
        actor_id=user.id,
        action="session.revoke_others",
        entity_type="user",
        entity_id=user.id,
        after={"revoked_count": count},
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"revoked_count": count})


@admin_router.get("", dependencies=[Depends(require_permission("sessions.manage"))])
async def list_user_sessions(user_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    rows = await service.list_sessions(db, user_id=user_id, current_family_id=None)
    return ok([_out(r) for r in rows])


@admin_router.delete(
    "/{family_id}", dependencies=[Depends(require_permission("sessions.manage"))]
)
async def revoke_user_session(
    user_id: int,
    family_id: str,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await service.admin_revoke_session(
        db, actor=actor, target_user_id=user_id, family_id=family_id
    )
    await write_audit(
        db,
        actor_id=actor.id,
        action="session.admin_revoke",
        entity_type="session",
        entity_id=family_id,
        after={"target_user_id": user_id},
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"revoked": True})
