"""Session (device) listing and revocation, built on refresh-token families."""

from __future__ import annotations

import asyncio

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
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_list_sessions_requires_authentication() -> None:
    with _client() as client:
        r = client.get("/api/v1/me/sessions")
        assert r.status_code == 401


def test_login_creates_one_session_marked_current() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "a@example.com", "password": PASSWORD}
        )
        login = _login(client, "a@example.com")

        r = client.get("/api/v1/me/sessions", headers=_auth(login["access_token"]))
        assert r.status_code == 200, r.text
        sessions = r.json()["data"]
        assert len(sessions) == 1
        assert sessions[0]["is_current"] is True


def test_two_logins_are_two_sessions() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "b@example.com", "password": PASSWORD}
        )
        login1 = _login(client, "b@example.com")
        login2 = _login(client, "b@example.com")

        r = client.get("/api/v1/me/sessions", headers=_auth(login2["access_token"]))
        sessions = r.json()["data"]
        assert len(sessions) == 2
        current = [s for s in sessions if s["is_current"]]
        assert len(current) == 1


def test_refresh_keeps_same_session_family() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "c@example.com", "password": PASSWORD}
        )
        login = _login(client, "c@example.com")

        r = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
        new_pair = r.json()["data"]

        r2 = client.get("/api/v1/me/sessions", headers=_auth(new_pair["access_token"]))
        assert len(r2.json()["data"]) == 1


def test_revoke_own_session_kills_it() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "d@example.com", "password": PASSWORD}
        )
        login1 = _login(client, "d@example.com")
        login2 = _login(client, "d@example.com")

        sessions = client.get(
            "/api/v1/me/sessions", headers=_auth(login2["access_token"])
        ).json()["data"]
        other_family = next(s["family_id"] for s in sessions if not s["is_current"])

        r = client.delete(
            f"/api/v1/me/sessions/{other_family}", headers=_auth(login2["access_token"])
        )
        assert r.status_code == 200

        # That session's refresh token no longer works.
        r2 = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login1["refresh_token"]}
        )
        assert r2.status_code == 401

        remaining = client.get(
            "/api/v1/me/sessions", headers=_auth(login2["access_token"])
        ).json()["data"]
        assert len(remaining) == 1


def test_revoke_others_keeps_current() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register", json={"email": "e@example.com", "password": PASSWORD}
        )
        _login(client, "e@example.com")
        _login(client, "e@example.com")
        current = _login(client, "e@example.com")

        r = client.post(
            "/api/v1/me/sessions/revoke-others", headers=_auth(current["access_token"])
        )
        assert r.status_code == 200
        assert r.json()["data"]["revoked_count"] == 2

        remaining = client.get(
            "/api/v1/me/sessions", headers=_auth(current["access_token"])
        ).json()["data"]
        assert len(remaining) == 1
        assert remaining[0]["is_current"] is True


def test_admin_can_revoke_lower_level_user_session() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root@example.com", "super_admin"))
        admin_token = _login(client, "root@example.com")["access_token"]

        client.post(
            "/api/v1/auth/register", json={"email": "junior@example.com", "password": PASSWORD}
        )
        junior_login = _login(client, "junior@example.com")
        junior_id = client.get(
            "/api/v1/me", headers=_auth(junior_login["access_token"])
        ).json()["data"]["id"]

        sessions = client.get(
            f"/api/v1/users/{junior_id}/sessions", headers=_auth(admin_token)
        ).json()["data"]
        assert len(sessions) == 1

        r = client.delete(
            f"/api/v1/users/{junior_id}/sessions/{sessions[0]['family_id']}",
            headers=_auth(admin_token),
        )
        assert r.status_code == 200

        r2 = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": junior_login["refresh_token"]}
        )
        assert r2.status_code == 401


def test_admin_cannot_revoke_equal_or_higher_level_user_session() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("admin1@example.com", "admin"))
        asyncio.run(_seed_user_with_role("admin2@example.com", "admin"))
        admin1_token = _login(client, "admin1@example.com")["access_token"]
        admin2_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "admin2@example.com")["access_token"])
        ).json()["data"]["id"]

        r = client.get(f"/api/v1/users/{admin2_id}/sessions", headers=_auth(admin1_token))
        assert r.status_code == 200
        # sessions.manage is enough to LIST, but revoking requires the same
        # hierarchy check as users module — verify on the delete call using a
        # made-up family id (the guard fires before the lookup either way).
        r2 = client.delete(
            f"/api/v1/users/{admin2_id}/sessions/does-not-matter", headers=_auth(admin1_token)
        )
        assert r2.status_code == 403
        assert r2.json()["error"]["code"] == "INSUFFICIENT_ROLE_LEVEL"
