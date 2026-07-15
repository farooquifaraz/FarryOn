"""``/api/v1/auth/sso/{provider}/login``, ``/callback``, and the native
mobile path ``/google/mobile``.

Thin by design — the OAuth/OIDC exchange itself lives here; account-linking
logic lives in modules/sso/service.py where it can be unit-tested without
live provider credentials.

Two Google flows, because a phone and a browser need different things:

- ``/google/login`` + ``/google/callback`` — authlib's redirect dance, for
  the web admin panel.
- ``/google/mobile`` — the Flutter app uses the native Google Sheet
  (`google_sign_in`), which hands back an ID token directly; there is no
  redirect to catch, so the app POSTs that token here and we verify it
  against Google's certs. Both funnel into the same
  :func:`service.link_or_create_user`, so the verified-email-only linking
  rule holds either way.
"""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.core.deps import get_db
from app.core.responses import AppError, ok
from app.modules.audit.service import write_audit
from app.modules.auth.service import issue_token_pair_for_sso
from app.modules.sso import service
from app.logging_conf import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth/sso", tags=["sso"])


class GoogleMobileRequest(BaseModel):
    """The ID token the native Google sign-in sheet returned to the app."""

    id_token: str


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


# Declared before the /{provider}/* routes so the literal path always wins.
@router.post("/google/mobile")
async def google_mobile(
    body: GoogleMobileRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Exchange a native Google ID token for a FarryOn session.

    The audience checked is ``google_client_id`` — the **Web** client id, not
    the Android/iOS one: `google_sign_in` is given it as `serverClientId`, and
    Google then mints the ID token with that as `aud` precisely so a backend
    can verify it. Getting this wrong is the usual cause of "Wrong recipient".
    """
    if not settings.google_client_id:
        raise AppError(
            "SSO_NOT_CONFIGURED", "Google sign-in isn't configured.", status_code=503
        )

    try:
        # Blocking: fetches (and caches) Google's signing certs. Verifies the
        # signature, issuer, audience and expiry — everything that makes the
        # claims below trustworthy. Never trust an unverified ID token.
        claims = await run_in_threadpool(
            google_id_token.verify_oauth2_token,
            body.id_token,
            google_requests.Request(),
            settings.google_client_id,
        )
    except ValueError as exc:
        # The token itself is bad: malformed, expired, wrong audience, or
        # forged. This is the only case where blaming the token is right.
        raise AppError(
            "INVALID_TOKEN", "That Google sign-in couldn't be verified.", status_code=401
        ) from exc
    except Exception as exc:  # noqa: BLE001
        # Anything else means we couldn't *reach* Google to check (cert store
        # broken, DNS, their outage). Saying "invalid token" here would blame
        # the user for our problem and, worse, look identical to a real
        # rejection — so fail as unavailable and let the app offer a retry.
        logger.warning("sso.google_verify_unreachable", error=str(exc))
        raise AppError(
            "SSO_UNAVAILABLE",
            "Couldn't reach Google to verify your sign-in. Try again.",
            status_code=503,
        ) from exc

    user = await service.link_or_create_user(
        db,
        provider="google",
        provider_user_id=claims["sub"],
        email=claims.get("email", ""),
        email_verified=bool(claims.get("email_verified", False)),
        display_name=claims.get("name"),
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
        after={"provider": "google", "flow": "mobile"},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return ok(pair.model_dump())


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
