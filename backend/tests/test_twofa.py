"""TOTP 2FA: enrollment, login challenge, recovery codes, admin force-disable."""

from __future__ import annotations

import asyncio

import pyotp
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db import base as db_base
from app.db.models import User, UserRole
from app.db.seed import seed_roles_and_permissions
from app.main import create_app

PASSWORD = "correct-horse-1"


def _client() -> TestClient:
    return TestClient(create_app())


async def _seed_user_with_role(email: str, role_name: str) -> None:
    sessionmaker = db_base.get_sessionmaker()
    async with sessionmaker() as db:
        roles = await seed_roles_and_permissions(db)
        user = User(
            external_id=f"user:{email}",
            email=email,
            password_hash=hash_password(PASSWORD),
            status="active",
        )
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=roles[role_name].id))
        await db.commit()


def _login(client: TestClient, email: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return r.json()["data"] if r.status_code == 200 else {"__status__": r.status_code, **r.json()}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _enroll_and_enable(client: TestClient, access_token: str) -> tuple[str, list[str]]:
    r = client.post("/api/v1/me/2fa/enroll", headers=_auth(access_token))
    assert r.status_code == 200, r.text
    secret = r.json()["data"]["secret"]

    code = pyotp.TOTP(secret).now()
    r2 = client.post(
        "/api/v1/me/2fa/confirm", headers=_auth(access_token), json={"code": code}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["enabled"] is True
    codes = r2.json()["data"]["recovery_codes"]
    assert len(codes) == 10
    return secret, codes


def test_enroll_requires_authentication() -> None:
    with _client() as client:
        r = client.post("/api/v1/me/2fa/enroll")
        assert r.status_code == 401


def test_confirm_with_wrong_code_fails() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "a@example.com", "password": PASSWORD}
        )
        token = _login(client, "a@example.com")["access_token"]

        client.post("/api/v1/me/2fa/enroll", headers=_auth(token))
        r = client.post(
            "/api/v1/me/2fa/confirm", headers=_auth(token), json={"code": "000000"}
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_CODE"


def test_login_with_2fa_enabled_requires_challenge() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "b@example.com", "password": PASSWORD}
        )
        token = _login(client, "b@example.com")["access_token"]
        secret, _codes = _enroll_and_enable(client, token)

        r = client.post(
            "/api/v1/auth/login", json={"email": "b@example.com", "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["two_factor_required"] is True
        assert "pending_token" in data
        assert "access_token" not in data

        code = pyotp.TOTP(secret).now()
        r2 = client.post(
            "/api/v1/auth/2fa/verify-login",
            json={"pending_token": data["pending_token"], "code": code},
        )
        assert r2.status_code == 200, r2.text
        assert "access_token" in r2.json()["data"]


def test_verify_login_wrong_code_fails() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "c@example.com", "password": PASSWORD}
        )
        token = _login(client, "c@example.com")["access_token"]
        _enroll_and_enable(client, token)

        pending = client.post(
            "/api/v1/auth/login", json={"email": "c@example.com", "password": PASSWORD}
        ).json()["data"]["pending_token"]

        r = client.post(
            "/api/v1/auth/2fa/verify-login",
            json={"pending_token": pending, "code": "000000"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_CODE"


def test_recovery_code_works_once() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "d@example.com", "password": PASSWORD}
        )
        token = _login(client, "d@example.com")["access_token"]
        _secret, codes = _enroll_and_enable(client, token)

        pending = client.post(
            "/api/v1/auth/login", json={"email": "d@example.com", "password": PASSWORD}
        ).json()["data"]["pending_token"]

        r = client.post(
            "/api/v1/auth/2fa/verify-login",
            json={"pending_token": pending, "code": codes[0]},
        )
        assert r.status_code == 200, r.text

        pending2 = client.post(
            "/api/v1/auth/login", json={"email": "d@example.com", "password": PASSWORD}
        ).json()["data"]["pending_token"]
        r2 = client.post(
            "/api/v1/auth/2fa/verify-login",
            json={"pending_token": pending2, "code": codes[0]},
        )
        assert r2.status_code == 400


def test_disable_requires_correct_password() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "e@example.com", "password": PASSWORD}
        )
        token = _login(client, "e@example.com")["access_token"]
        _enroll_and_enable(client, token)

        r = client.post(
            "/api/v1/me/2fa/disable", headers=_auth(token), json={"password": "wrong"}
        )
        assert r.status_code == 401

        r2 = client.post(
            "/api/v1/me/2fa/disable", headers=_auth(token), json={"password": PASSWORD}
        )
        assert r2.status_code == 200

        # Login no longer requires 2FA.
        r3 = client.post(
            "/api/v1/auth/login", json={"email": "e@example.com", "password": PASSWORD}
        )
        assert "access_token" in r3.json()["data"]


def test_admin_force_disable_lower_level_user() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root@example.com", "super_admin"))
        admin_token = _login(client, "root@example.com")["access_token"]

        client.post(
            "/api/v1/auth/register", json={"email": "junior@example.com", "password": PASSWORD}
        )
        junior_token = _login(client, "junior@example.com")["access_token"]
        _enroll_and_enable(client, junior_token)
        junior_id = client.get(
            "/api/v1/me", headers=_auth(junior_token)
        ).json()["data"]["id"]

        r = client.patch(
            f"/api/v1/users/{junior_id}/2fa/disable", headers=_auth(admin_token)
        )
        assert r.status_code == 200, r.text

        r2 = client.post(
            "/api/v1/auth/login", json={"email": "junior@example.com", "password": PASSWORD}
        )
        assert "access_token" in r2.json()["data"]
