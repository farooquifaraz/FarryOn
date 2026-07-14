"""``POST /api/v1/users/{user_id}/impersonate``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.deps import get_current_user, get_db, require_permission
from app.core.responses import ok
from app.db.models import User
from app.modules.audit.service import write_audit
from app.modules.impersonation import service

router = APIRouter(prefix="/users/{user_id}", tags=["impersonation"])


@router.post(
    "/impersonate", dependencies=[Depends(require_permission("users.impersonate"))]
)
async def impersonate_endpoint(
    user_id: int,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    result = await service.start_impersonation(db, settings, actor=actor, target_user_id=user_id)
    await write_audit(
        db,
        actor_id=user_id,
        impersonator_id=actor.id,
        action="impersonation.start",
        entity_type="user",
        entity_id=user_id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return ok(result.model_dump())
