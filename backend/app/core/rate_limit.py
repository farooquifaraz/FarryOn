"""DB-backed login rate limiting (the documented stand-in for Redis — see
docs/ADMIN_USER_MODULE_ARCHITECTURE.md, decision table).

Windowed count query against ``login_attempts``. Locks by email AND,
separately, by IP, so a distributed attempt against one account or a
credential-stuffing sweep from one IP both get throttled.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LoginAttempt

MAX_ATTEMPTS_PER_WINDOW = 5
WINDOW_MINUTES = 15


async def is_locked_out(db: AsyncSession, *, email: str, ip: str | None) -> bool:
    """True if either the account or the IP has hit the failure threshold."""
    window_start = datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MINUTES)

    email_failures = (
        await db.execute(
            select(func.count()).select_from(LoginAttempt).where(
                LoginAttempt.email == email.lower(),
                LoginAttempt.success.is_(False),
                LoginAttempt.created_at > window_start,
            )
        )
    ).scalar_one()
    if email_failures >= MAX_ATTEMPTS_PER_WINDOW:
        return True

    if ip:
        ip_failures = (
            await db.execute(
                select(func.count()).select_from(LoginAttempt).where(
                    LoginAttempt.ip == ip,
                    LoginAttempt.success.is_(False),
                    LoginAttempt.created_at > window_start,
                )
            )
        ).scalar_one()
        if ip_failures >= MAX_ATTEMPTS_PER_WINDOW * 4:
            # IP threshold is looser: many legit users can share an IP
            # (NAT/office wifi); this only stops a broad sweep.
            return True

    return False


async def record_attempt(
    db: AsyncSession, *, email: str, ip: str | None, success: bool
) -> None:
    db.add(LoginAttempt(email=email.lower(), ip=ip, success=success))
    await db.flush()
