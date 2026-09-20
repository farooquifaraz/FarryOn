"""Set (reset) the password of an existing account, by email.

Usage::

    python -m scripts.set_password farooqui.faraz@gmail.com

The new password is asked for on the terminal (not echoed, not logged) so it
never lands in a shell history or a log line. Where there is no terminal
(a container exec without -t, automation) put it in the NEW_PASSWORD
environment variable instead. Works against whatever
DATABASE_URL the environment points at — the local SQLite file from a
developer's checkout, or Postgres from inside the backend container on the
VPS::

    docker compose -f docker-compose.prod.yml -f deploy/hostinger/compose.behind-proxy.yml \\
        exec backend python -m scripts.set_password someone@example.com

The account must already exist (use ``scripts.seed_admin`` with
FIRST_SUPER_ADMIN_* to create one). The account is also marked active and
email-verified, since a password reset by the operator is a stronger
statement than a clicked link, and every refresh token it holds is revoked
so a stolen session does not outlive the old password.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update

from app.config import get_settings
from app.core.security import hash_password
from app.db.base import dispose_db, get_sessionmaker, init_db
from app.db.models import RefreshToken, User

MIN_LENGTH = 10


async def main(email: str, password: str) -> int:
    settings = get_settings()
    await init_db(settings)
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as db:
            user = (
                await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
            ).scalar_one_or_none()
            if user is None:
                print(f"no account with email {email}", file=sys.stderr)
                return 2
            now = datetime.now(timezone.utc)
            user.password_hash = hash_password(password)
            user.status = "active"
            user.email_verified_at = user.email_verified_at or now
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await db.commit()
            print(f"password set for {email} (user id {user.id}); existing sessions signed out")
            return 0
    finally:
        await dispose_db()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m scripts.set_password <email>", file=sys.stderr)
        sys.exit(1)
    first = os.environ.get("NEW_PASSWORD") or ""
    if not first:
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Again: ")
        if first != second:
            print("passwords do not match", file=sys.stderr)
            sys.exit(1)
    if len(first) < MIN_LENGTH:
        print(f"use at least {MIN_LENGTH} characters", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(main(sys.argv[1].strip().lower(), first)))
