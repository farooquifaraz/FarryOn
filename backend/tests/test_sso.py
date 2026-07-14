"""SSO account-linking logic (unit-level — no live Google/Microsoft
credentials needed) and the router's "not configured" guard.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.responses import AppError
from app.db import base as db_base
from app.db.models import OAuthAccount, User
from app.main import create_app
from app.modules.sso.service import link_or_create_user


def _client() -> TestClient:
    return TestClient(create_app())


def test_sso_login_503_when_not_configured() -> None:
    with _client() as client:
        r = client.get("/api/v1/auth/sso/google/login", follow_redirects=False)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "SSO_NOT_CONFIGURED"


def test_sso_login_404_for_unknown_provider() -> None:
    with _client() as client:
        r = client.get("/api/v1/auth/sso/facebook/login", follow_redirects=False)
        assert r.status_code == 404


def test_unverified_email_rejected() -> None:
    async def _run() -> None:
        sessionmaker = db_base.get_sessionmaker()
        async with sessionmaker() as db:
            with pytest.raises(AppError) as exc_info:
                await link_or_create_user(
                    db,
                    provider="google",
                    provider_user_id="g-123",
                    email="new@example.com",
                    email_verified=False,
                    display_name="New Person",
                )
            assert exc_info.value.code == "EMAIL_NOT_VERIFIED"

    asyncio.run(_run())


def test_new_verified_email_creates_user() -> None:
    async def _run() -> None:
        sessionmaker = db_base.get_sessionmaker()
        async with sessionmaker() as db:
            user = await link_or_create_user(
                db,
                provider="google",
                provider_user_id="g-456",
                email="fresh@example.com",
                email_verified=True,
                display_name="Fresh Person",
            )
            await db.commit()
            assert user.email == "fresh@example.com"
            assert user.email_verified_at is not None
            assert user.password_hash is None

            link = (
                await db.execute(
                    select(OAuthAccount).where(OAuthAccount.user_id == user.id)
                )
            ).scalar_one()
            assert link.provider == "google"
            assert link.provider_user_id == "g-456"

    asyncio.run(_run())


def test_second_login_same_identity_returns_same_user() -> None:
    async def _run() -> None:
        sessionmaker = db_base.get_sessionmaker()
        async with sessionmaker() as db:
            user1 = await link_or_create_user(
                db,
                provider="microsoft",
                provider_user_id="ms-789",
                email="repeat@example.com",
                email_verified=True,
                display_name="Repeat Person",
            )
            await db.commit()

        async with sessionmaker() as db:
            user2 = await link_or_create_user(
                db,
                provider="microsoft",
                provider_user_id="ms-789",
                email="repeat@example.com",
                email_verified=True,
                display_name="Repeat Person",
            )
            await db.commit()
            assert user2.id == user1.id

            count = (
                await db.execute(
                    select(OAuthAccount).where(OAuthAccount.user_id == user1.id)
                )
            ).scalars().all()
            assert len(count) == 1  # not re-linked/duplicated on second login

    asyncio.run(_run())


def test_cannot_auto_merge_into_unverified_existing_account() -> None:
    async def _run() -> None:
        sessionmaker = db_base.get_sessionmaker()
        async with sessionmaker() as db:
            # An existing account with this email but an UNVERIFIED email
            # (e.g. mid-registration, never clicked the verify link).
            db.add(
                User(
                    external_id="user:unverified-target",
                    email="unverified@example.com",
                    password_hash="not-a-real-hash",
                    status="active",
                )
            )
            await db.commit()

        async with sessionmaker() as db:
            with pytest.raises(AppError) as exc_info:
                await link_or_create_user(
                    db,
                    provider="google",
                    provider_user_id="g-999",
                    email="unverified@example.com",
                    email_verified=True,
                    display_name="Attacker Or Coincidence",
                )
            assert exc_info.value.code == "EMAIL_NOT_VERIFIED"

    asyncio.run(_run())
