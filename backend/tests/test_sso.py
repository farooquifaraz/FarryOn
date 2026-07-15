"""SSO account-linking logic (unit-level — no live Google/Microsoft
credentials needed) and the router's "not configured" guard.
"""

from __future__ import annotations

import asyncio
import contextlib

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


def test_google_mobile_503_when_not_configured() -> None:
    """The native app path must fail loudly-but-cleanly, not 500, when no
    client id is set — the app hides its Google button on this signal."""
    with _client() as client:
        r = client.post(
            "/api/v1/auth/sso/google/mobile", json={"id_token": "anything"}
        )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "SSO_NOT_CONFIGURED"


@contextlib.contextmanager
def _google_configured(verify):
    """Configure a Google client id and stub the ID-token verification, so
    these tests never touch the network or need real Google credentials."""
    import os

    from app.config import get_settings
    from app.modules.sso import router as sso_router

    os.environ["GOOGLE_CLIENT_ID"] = "test-web.apps.googleusercontent.com"
    get_settings.cache_clear()
    original = sso_router.google_id_token.verify_oauth2_token
    sso_router.google_id_token.verify_oauth2_token = verify
    try:
        yield
    finally:
        sso_router.google_id_token.verify_oauth2_token = original
        os.environ.pop("GOOGLE_CLIENT_ID", None)
        get_settings.cache_clear()


def test_google_mobile_rejects_a_forged_id_token() -> None:
    """A token google-auth won't verify (forged, expired, wrong audience) must
    never mint a session — it raises ValueError for all of those."""

    def _reject(*_args, **_kwargs):
        raise ValueError("Wrong recipient")

    with _google_configured(_reject), _client() as client:
        r = client.post(
            "/api/v1/auth/sso/google/mobile", json={"id_token": "forged"}
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_TOKEN"


def test_google_mobile_reports_unreachable_google_as_503_not_401() -> None:
    """If we can't REACH Google to check, that's our outage, not a bad token.
    A 401 here would blame the user and be indistinguishable from a forgery."""

    def _network_down(*_args, **_kwargs):
        raise OSError("CA bundle missing")

    with _google_configured(_network_down), _client() as client:
        r = client.post(
            "/api/v1/auth/sso/google/mobile", json={"id_token": "fine"}
        )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "SSO_UNAVAILABLE"


def test_google_mobile_signs_in_a_verified_google_account() -> None:
    """The happy path: verified claims create the account and return tokens."""

    def _accept(*_args, **_kwargs):
        return {
            "sub": "google-uid-123",
            "email": "gmail.person@example.com",
            "email_verified": True,
            "name": "Gmail Person",
        }

    with _google_configured(_accept), _client() as client:
        r = client.post(
            "/api/v1/auth/sso/google/mobile", json={"id_token": "good"}
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["access_token"] and data["refresh_token"]

        # And the account is real: it can now be used like any other session.
        me = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert me.json()["data"]["email"] == "gmail.person@example.com"


def test_google_mobile_refuses_an_unverified_google_email() -> None:
    """Google says the email isn't verified — the linking rule must hold even
    though Google itself vouched for the token."""

    def _accept_unverified(*_args, **_kwargs):
        return {
            "sub": "google-uid-456",
            "email": "unverified.person@example.com",
            "email_verified": False,
            "name": "Unverified Person",
        }

    with _google_configured(_accept_unverified), _client() as client:
        r = client.post(
            "/api/v1/auth/sso/google/mobile", json={"id_token": "good"}
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


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
