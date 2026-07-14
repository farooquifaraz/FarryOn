"""``/api/v1/auth/sso/{provider}/login`` and ``/callback``.

Thin by design — the OAuth/OIDC exchange itself (via authlib) lives here;
account-linking logic lives in modules/sso/service.py where it can be unit-
tested without live provider credentials.
"""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.deps import get_db
from app.core.responses import AppError, ok
from app.modules.audit.service import write_audit
from app.modules.auth.service import issue_token_pair_for_sso
from app.modules.sso import service

router = APIRouter(prefix="/auth/sso", tags=["sso"])


def _build_client(settings: Settings, provider: str):
    oauth = OAuth()
    if provider == "google":
        if not settings.google_client_id or not settings.google_client_secret:
            raise AppError(
                "SSO_NOT_CONFIGURED", "Google sign-in isn't configured.", status_code=503
            )
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    elif provider == "microsoft":
        if not settings.microsoft_client_id or not settings.microsoft_client_secret:
            raise AppError(
                "SSO_NOT_CONFIGURED", "Microsoft sign-in isn't configured.", status_code=503
            )
        oauth.register(
            name="microsoft",
            client_id=settings.microsoft_client_id,
            client_secret=settings.microsoft_client_secret,
            server_metadata_url=(
                f"https://login.microsoftonline.com/{settings.microsoft_tenant}"
                "/v2.0/.well-known/openid-configuration"
            ),
            client_kwargs={"scope": "openid email profile"},
        )
    else:
        raise AppError("NOT_FOUND", f"Unknown SSO provider: {provider}", status_code=404)
    return oauth.create_client(provider)


def _redirect_uri(settings: Settings, provider: str) -> str:
    return f"{settings.sso_redirect_base_url}/api/v1/auth/sso/{provider}/callback"


@router.get("/{provider}/login")
async def sso_login(
    provider: str, request: Request, settings: Settings = Depends(get_settings)
):
    client = _build_client(settings, provider)
    return await client.authorize_redirect(request, _redirect_uri(settings, provider))


@router.get("/{provider}/callback", response_model=None)
async def sso_callback(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict | RedirectResponse:
    client = _build_client(settings, provider)
    token = await client.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if userinfo is None:
        userinfo = await client.userinfo(token=token)

    user = await service.link_or_create_user(
        db,
        provider=provider,
        provider_user_id=userinfo["sub"],
        email=userinfo["email"],
        email_verified=bool(userinfo.get("email_verified", False)),
        display_name=userinfo.get("name"),
    )
    pair = await issue_token_pair_for_sso(
        db,
        settings,
        user=user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    await write_audit(
        db,
        actor_id=user.id,
        action="auth.sso_login",
        entity_type="user",
        entity_id=user.id,
        after={"provider": provider},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    if settings.sso_frontend_success_url:
        return RedirectResponse(
            f"{settings.sso_frontend_success_url}"
            f"?access_token={pair.access_token}&refresh_token={pair.refresh_token}"
        )
    return ok(pair.model_dump())
