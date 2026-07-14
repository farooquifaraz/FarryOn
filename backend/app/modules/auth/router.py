"""``/api/v1/auth/*`` and ``/api/v1/me`` endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.deps import get_current_user, get_db
from app.core.responses import ok
from app.core.security import decode_token
from app.db.models import Role, User, UserRole
from app.modules.audit.service import write_audit
from app.modules.auth import service
from app.modules.auth.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UserPublic,
    VerifyEmailRequest,
)
from app.modules.twofa.schemas import VerifyLoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _to_public(db: AsyncSession, user: User) -> UserPublic:
    from app.modules.rbac.service import get_user_permission_codes

    role_names = (
        await db.execute(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(
                UserRole.user_id == user.id
            )
        )
    ).scalars().all()
    return UserPublic(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        email_verified=user.email_verified_at is not None,
        roles=list(role_names),
        permissions=sorted(await get_user_permission_codes(db, user.id)),
    )


@router.post("/register")
async def register_endpoint(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    user = await service.register(
        db, settings, email=body.email, password=body.password, display_name=body.display_name
    )
    await write_audit(
        db,
        actor_id=user.id,
        action="auth.register",
        entity_type="user",
        entity_id=user.id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(await _to_public(db, user))


@router.post("/login")
async def login_endpoint(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    pair = await service.login(
        db,
        settings,
        email=body.email,
        password=body.password,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if isinstance(pair, service.TokenPairResponse):
        claims = decode_token(settings=settings, token=pair.access_token)
        await write_audit(
            db,
            actor_id=int(claims["sub"]) if claims else None,
            action="auth.login",
            entity_type="user",
            entity_id=claims.get("sub") if claims else None,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    return ok(pair.model_dump())


@router.post("/2fa/verify-login")
async def verify_login_2fa_endpoint(
    body: VerifyLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    pair = await service.verify_login_2fa(
        db,
        settings,
        pending_token=body.pending_token,
        code=body.code,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    claims = decode_token(settings=settings, token=pair.access_token)
    await write_audit(
        db,
        actor_id=int(claims["sub"]) if claims else None,
        action="auth.login",
        entity_type="user",
        entity_id=claims.get("sub") if claims else None,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(pair.model_dump())


@router.post("/refresh")
async def refresh_endpoint(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    pair = await service.refresh(
        db,
        settings,
        raw_refresh_token=body.refresh_token,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(pair.model_dump())


@router.post("/logout")
async def logout_endpoint(
    body: LogoutRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    user_id = await service.logout(db, raw_refresh_token=body.refresh_token)
    if user_id is not None:
        await write_audit(
            db,
            actor_id=user_id,
            action="auth.logout",
            entity_type="user",
            entity_id=user_id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    return ok({"logged_out": True})


@router.post("/verify-email")
async def verify_email_endpoint(
    body: VerifyEmailRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    await service.verify_email(db, raw_token=body.token)
    return ok({"verified": True})


@router.post("/forgot-password")
async def forgot_password_endpoint(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    await service.forgot_password(db, settings, email=body.email)
    # Always the same response — existence of the email is never revealed.
    return ok({"message": "If that email exists, a reset link has been sent."})


@router.post("/reset-password")
async def reset_password_endpoint(
    body: ResetPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    user_id = await service.reset_password(
        db, raw_token=body.token, new_password=body.new_password
    )
    await write_audit(
        db,
        actor_id=user_id,
        action="auth.password_reset",
        entity_type="user",
        entity_id=user_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"reset": True})


@me_router.get("")
async def get_me_endpoint(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    return ok(await _to_public(db, user))
