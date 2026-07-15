"""Admin "login as user" — guard rails, the ``act`` JWT claim, and audit
logging of both identities.
"""

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
    return r.json()["data"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_impersonate_requires_permission() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("plain@example.com", "user"))
        client.post(
            "/api/v1/auth/register", json={"email": "target@example.com", "password": PASSWORD}
        )
        target_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "target@example.com")["access_token"])
        ).json()["data"]["id"]

        plain_token = _login(client, "plain@example.com")["access_token"]
        r = client.post(f"/api/v1/users/{target_id}/impersonate", headers=_auth(plain_token))
        assert r.status_code == 403


def test_cannot_impersonate_self() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root@example.com", "super_admin"))
        admin_token = _login(client, "root@example.com")["access_token"]
        admin_id = client.get("/api/v1/me", headers=_auth(admin_token)).json()["data"]["id"]

        r = client.post(f"/api/v1/users/{admin_id}/impersonate", headers=_auth(admin_token))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SELF_IMPERSONATION_FORBIDDEN"


def test_cannot_impersonate_equal_or_higher_level_user() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("admin1@example.com", "admin"))
        asyncio.run(_seed_user_with_role("admin2@example.com", "admin"))
        admin1_token = _login(client, "admin1@example.com")["access_token"]
        admin2_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "admin2@example.com")["access_token"])
        ).json()["data"]["id"]

        r = client.post(f"/api/v1/users/{admin2_id}/impersonate", headers=_auth(admin1_token))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INSUFFICIENT_ROLE_LEVEL"


def test_cannot_impersonate_suspended_user() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root2@example.com", "super_admin"))
        admin_token = _login(client, "root2@example.com")["access_token"]

        client.post(
            "/api/v1/auth/register", json={"email": "junior@example.com", "password": PASSWORD}
        )
        junior_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "junior@example.com")["access_token"])
        ).json()["data"]["id"]
        client.patch(
            f"/api/v1/users/{junior_id}",
            headers=_auth(admin_token),
            json={"status": "suspended"},
        )

        r = client.post(f"/api/v1/users/{junior_id}/impersonate", headers=_auth(admin_token))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "USER_SUSPENDED"


def test_successful_impersonation_authenticates_as_target_and_is_audited() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root3@example.com", "super_admin"))
        admin_token = _login(client, "root3@example.com")["access_token"]
        admin_id = client.get("/api/v1/me", headers=_auth(admin_token)).json()["data"]["id"]

        client.post(
            "/api/v1/auth/register", json={"email": "junior2@example.com", "password": PASSWORD}
        )
        junior_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "junior2@example.com")["access_token"])
        ).json()["data"]["id"]

        r = client.post(f"/api/v1/users/{junior_id}/impersonate", headers=_auth(admin_token))
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["impersonating_user_id"] == junior_id
        assert "refresh_token" not in data  # impersonation sessions don't rotate

        # The impersonation token authenticates AS the target...
        me = client.get("/api/v1/me", headers=_auth(data["access_token"])).json()["data"]
        assert me["id"] == junior_id
        assert me["email"] == "junior2@example.com"

        # ...but the audit trail shows both identities.
        logs = client.get(
            "/api/v1/audit-logs?action=impersonation.start", headers=_auth(admin_token)
        ).json()["data"]
        assert len(logs) == 1
        assert logs[0]["actor_id"] == junior_id
        assert logs[0]["impersonator_id"] == admin_id
