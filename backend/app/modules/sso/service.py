"""Account linking for SSO callbacks — deliberately decoupled from the
actual OAuth network exchange (see modules/sso/router.py) so this, the part
that matters for correctness/security, is unit-testable without live Google/
Microsoft credentials.

Rule (from docs/ADMIN_USER_MODULE_ARCHITECTURE.md, restated from the source
spec): account linking happens by VERIFIED email match only. An unverified
email — on either side — is never auto-merged into an existing account.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import AppError
from app.db.models import OAuthAccount, User
from app.logging_conf import get_logger

logger = get_logger(__name__)

SUPPORTED_PROVIDERS = ("google", "microsoft")


async def link_or_create_user(
    db: AsyncSession,
    *,
    provider: str,
    provider_user_id: str,
    email: str,
    email_verified: bool,
    display_name: str | None,
) -> User:
    if provider not in SUPPORTED_PROVIDERS:
        raise AppError("NOT_FOUND", f"Unknown SSO provider: {provider}", status_code=404)
    if not email_verified:
        raise AppError(
            "EMAIL_NOT_VERIFIED",
            "Your account with this provider must have a verified email to sign in.",
            status_code=400,
        )
    email_norm = email.lower()

    # 1. Already linked — same provider identity signing in again.
    link = (
        await db.execute(
            select(OAuthAccount).where(
                OAuthAccount.provider == provider,
                OAuthAccount.provider_user_id == provider_user_id,
            )
        )
    ).scalar_one_or_none()
    if link is not None:
        user = await db.get(User, link.user_id)
        if user is None or user.deleted_at is not None:
            raise AppError("NOT_FOUND", "This account no longer exists.", status_code=404)
        if user.status in ("suspended", "deactivated"):
            raise AppError(
                "USER_SUSPENDED", "This account is no longer active.", status_code=403
            )
        return user

    # 2. First time seeing this provider identity — link to an existing
    #    FarryOn account ONLY if that account's own email is independently
    #    verified (never trust the provider's claim alone to merge into
    #    someone else's pre-existing password-based account).
    existing_user = (
        await db.execute(
            select(User).where(User.email == email_norm, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if existing_user is not None:
        if existing_user.email_verified_at is None:
            raise AppError(
                "EMAIL_NOT_VERIFIED",
                "Verify your FarryOn account's email before linking a social login.",
                status_code=400,
            )
        db.add(
            OAuthAccount(
                user_id=existing_user.id,
                provider=provider,
                provider_user_id=provider_user_id,
                email=email_norm,
            )
        )
        await db.flush()
        logger.info("sso.linked_existing_user", user_id=existing_user.id, provider=provider)
        return existing_user

    # 3. Nobody has this email yet — provision a new account. The provider
    #    already verified the email, so we can trust it here.
    now = datetime.now(timezone.utc)
    user = User(
        external_id=f"sso:{provider}:{provider_user_id}",
        email=email_norm,
        password_hash=None,
        display_name=display_name,
        status="active",
        email_verified_at=now,
    )
    db.add(user)
    await db.flush()
    db.add(
        OAuthAccount(
            user_id=user.id, provider=provider, provider_user_id=provider_user_id, email=email_norm
        )
    )
    await db.flush()
    logger.info("sso.created_user", user_id=user.id, provider=provider)
    return user
