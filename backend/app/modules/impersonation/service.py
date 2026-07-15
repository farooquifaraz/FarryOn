"""Mint an impersonation token: no refresh token, no session/family (see
app/modules/sessions/service.py) — it's short-lived by design and simply
expires; there's no server-side "stop" call because the frontend already
holds the admin's own real token and just switches back to it (the "Return
to admin" banner button, per docs/ADMIN_USER_MODULE_ARCHITECTURE.md).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.responses import AppError
from app.core.security import encode_token
from app.db.models import User
from app.modules.impersonation.schemas import ImpersonationTokenResponse
from app.modules.rbac import service as rbac_service
from app.modules.users.service import get_user_or_404
from app.logging_conf import get_logger

logger = get_logger(__name__)


async def start_impersonation(
    db: AsyncSession, settings: Settings, *, actor: User, target_user_id: int
) -> ImpersonationTokenResponse:
    if actor.id == target_user_id:
        raise AppError(
            "SELF_IMPERSONATION_FORBIDDEN", "You cannot impersonate yourself.", status_code=403
        )

    target = await get_user_or_404(db, target_user_id)
    if target.status in ("suspended", "deactivated"):
        raise AppError(
            "USER_SUSPENDED", "Cannot impersonate an inactive account.", status_code=403
        )

    actor_level, actor_is_system = await rbac_service.get_role_context(db, actor.id)
    target_level, _ = await rbac_service.get_role_context(db, target.id)
    if not actor_is_system and target_level >= actor_level:
        raise AppError(
            "INSUFFICIENT_ROLE_LEVEL",
            "You cannot impersonate a user with an equal or higher role than your own.",
            status_code=403,
        )

    access = encode_token(
        settings=settings,
        user_id=target.id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims={"act": {"impersonator_id": actor.id}},
    )
    logger.info("impersonation.started", actor_id=actor.id, target_user_id=target.id)
    return ImpersonationTokenResponse(
        access_token=access,
        expires_in=settings.access_token_expire_minutes * 60,
        impersonating_user_id=target.id,
    )
