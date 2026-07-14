"""``/api/v1/me/2fa/*`` (self-service) and admin force-disable."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_permission
from app.core.responses import ok
from app.db.models import User
from app.modules.audit.service import write_audit
from app.modules.twofa import service
from app.modules.twofa.schemas import ConfirmEnrollRequest, DisableRequest

me_router = APIRouter(prefix="/me/2fa", tags=["2fa"])
admin_router = APIRouter(prefix="/users/{user_id}/2fa", tags=["2fa"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@me_router.post("/enroll")
async def enroll_endpoint(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    secret, uri, qr_b64 = await service.enroll(db, user=user)
    return ok({"secret": secret, "otpauth_uri": uri, "qr_code_png_base64": qr_b64})


@me_router.post("/confirm")
async def confirm_endpoint(
    body: ConfirmEnrollRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    codes = await service.confirm_enroll(db, user=user, code=body.code)
    await write_audit(
        db,
        actor_id=user.id,
        action="twofa.enable",
        entity_type="user",
        entity_id=user.id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"enabled": True, "recovery_codes": codes})


@me_router.post("/disable")
async def disable_endpoint(
    body: DisableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await service.disable(db, user=user, password=body.password)
    await write_audit(
        db,
        actor_id=user.id,
        action="twofa.disable",
        entity_type="user",
        entity_id=user.id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"disabled": True})


@admin_router.patch(
    "/disable", dependencies=[Depends(require_permission("users.update"))]
)
async def admin_disable_endpoint(
    user_id: int,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await service.admin_force_disable(db, actor=actor, target_user_id=user_id)
    await write_audit(
        db,
        actor_id=actor.id,
        action="twofa.admin_disable",
        entity_type="user",
        entity_id=user_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"disabled": True})
