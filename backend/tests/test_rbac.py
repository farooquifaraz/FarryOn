"""RBAC engine: require_permission enforcement, role/permission CRUD, and the
guard rails protecting the admin hierarchy (self-edit block, role-level
comparison, system-role lock, last-super-admin protection).
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db import base as db_base
from app.db.models import Role, User, UserRole
from app.db.seed import seed_roles_and_permissions
from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


async def _seed_user_with_role(email: str, role_name: str) -> str:
    """Seed default roles/permissions (idempotent) and create a user holding
    ``role_name``, bypassing the API (mirrors how the first super_admin is
    bootstrapped in production via scripts/seed_admin.py). Returns the raw
    password so the caller can log in through the real endpoint.
    """
    password = "correct-horse-1"
    sessionmaker = db_base.get_sessionmaker()
    async with sessionmaker() as db:
        roles = await seed_roles_and_permissions(db)
        user = User(
            external_id=f"user:{email}",
            email=email,
            password_hash=hash_password(password),
            status="active",
        )
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=roles[role_name].id))
        await db.commit()
    return password


def _login(client: TestClient, email: str, password: str) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_roles_endpoint_requires_authentication() -> None:
    with _client() as client:
        r = client.get("/api/v1/roles")
        assert r.status_code == 401


def test_plain_user_lacks_permission_to_list_roles() -> None:
    with _client() as client:

        password = asyncio.run(
            _seed_user_with_role("plain@example.com", "user")
        )
        token = _login(client, "plain@example.com", password)
        r = client.get("/api/v1/roles", headers=_auth(token))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "FORBIDDEN"


def test_super_admin_sees_seeded_roles_and_permissions() -> None:
    with _client() as client:

        password = asyncio.run(
            _seed_user_with_role("root@example.com", "super_admin")
        )
        token = _login(client, "root@example.com", password)

        r = client.get("/api/v1/roles", headers=_auth(token))
        assert r.status_code == 200, r.text
        names = {row["name"] for row in r.json()["data"]}
        assert names == {"super_admin", "admin", "manager", "user"}

        r = client.get("/api/v1/permissions", headers=_auth(token))
        assert r.status_code == 200
        assert len(r.json()["data"]) == 14


def test_create_update_and_delete_custom_role() -> None:
    with _client() as client:

        password = asyncio.run(
            _seed_user_with_role("root2@example.com", "super_admin")
        )
        token = _login(client, "root2@example.com", password)

        r = client.post(
            "/api/v1/roles",
            headers=_auth(token),
            json={
                "name": "support",
                "description": "Read-only support staff",
                "level": 20,
                "permission_codes": ["users.read", "audit.read"],
            },
        )
        assert r.status_code == 200, r.text
        role_id = r.json()["data"]["id"]
        assert set(r.json()["data"]["permissions"]) == {"users.read", "audit.read"}

        r = client.patch(
            f"/api/v1/roles/{role_id}",
            headers=_auth(token),
            json={"permission_codes": ["users.read"]},
        )
        assert r.status_code == 200
        assert r.json()["data"]["permissions"] == ["users.read"]

        r = client.delete(f"/api/v1/roles/{role_id}", headers=_auth(token))
        assert r.status_code == 200


def test_system_role_cannot_be_edited_or_deleted() -> None:
    with _client() as client:

        password = asyncio.run(
            _seed_user_with_role("root3@example.com", "super_admin")
        )
        token = _login(client, "root3@example.com", password)

        sessionmaker = db_base.get_sessionmaker()

        async def _get_super_admin_id() -> int:
            async with sessionmaker() as db:
                role = (
                    await db.execute(select(Role).where(Role.name == "super_admin"))
                ).scalar_one()
                return role.id

        super_admin_id = asyncio.run(_get_super_admin_id())

        r = client.patch(
            f"/api/v1/roles/{super_admin_id}",
            headers=_auth(token),
            json={"description": "nope"},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SYSTEM_ROLE_LOCKED"

        r = client.delete(f"/api/v1/roles/{super_admin_id}", headers=_auth(token))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SYSTEM_ROLE_LOCKED"


def test_cannot_delete_role_still_assigned_to_a_user() -> None:
    with _client() as client:

        password = asyncio.run(
            _seed_user_with_role("root4@example.com", "super_admin")
        )
        token = _login(client, "root4@example.com", password)

        sessionmaker = db_base.get_sessionmaker()

        async def _get_manager_id() -> int:
            async with sessionmaker() as db:
                role = (
                    await db.execute(select(Role).where(Role.name == "manager"))
                ).scalar_one()
                return role.id

        manager_id = asyncio.run(_get_manager_id())

        client.post(
            "/api/v1/auth/register",
            json={"email": "staffer@example.com", "password": "correct-horse-1"},
        )
        staffer_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "staffer@example.com", "correct-horse-1"))
        ).json()["data"]["id"]

        client.put(
            f"/api/v1/users/{staffer_id}/roles",
            headers=_auth(token),
            json={"role_ids": [manager_id]},
        )

        r = client.delete(f"/api/v1/roles/{manager_id}", headers=_auth(token))
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "ROLE_IN_USE"


def test_self_role_edit_is_forbidden() -> None:
    with _client() as client:

        password = asyncio.run(
            _seed_user_with_role("self@example.com", "super_admin")
        )
        token = _login(client, "self@example.com", password)
        me_id = client.get("/api/v1/me", headers=_auth(token)).json()["data"]["id"]

        r = client.put(
            f"/api/v1/users/{me_id}/roles", headers=_auth(token), json={"role_ids": []}
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SELF_ROLE_EDIT_FORBIDDEN"


def test_admin_cannot_modify_equal_or_higher_level_user() -> None:
    with _client() as client:

        admin_password = asyncio.run(_seed_user_with_role("admin1@example.com", "admin"))
        # A second admin — same level (80) as the first.
        asyncio.run(_seed_user_with_role("admin2@example.com", "admin"))

        admin1_token = _login(client, "admin1@example.com", admin_password)
        admin2_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "admin2@example.com", admin_password))
        ).json()["data"]["id"]

        r = client.put(
            f"/api/v1/users/{admin2_id}/roles",
            headers=_auth(admin1_token),
            json={"role_ids": []},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INSUFFICIENT_ROLE_LEVEL"


def test_admin_can_modify_a_lower_level_user() -> None:
    with _client() as client:

        admin_password = asyncio.run(_seed_user_with_role("admin3@example.com", "admin"))
        admin_token = _login(client, "admin3@example.com", admin_password)

        client.post(
            "/api/v1/auth/register",
            json={"email": "junior@example.com", "password": "correct-horse-1"},
        )
        junior_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "junior@example.com", "correct-horse-1"))
        ).json()["data"]["id"]

        sessionmaker = db_base.get_sessionmaker()

        async def _get_manager_id() -> int:
            async with sessionmaker() as db:
                role = (
                    await db.execute(select(Role).where(Role.name == "manager"))
                ).scalar_one()
                return role.id

        manager_id = asyncio.run(_get_manager_id())

        r = client.put(
            f"/api/v1/users/{junior_id}/roles",
            headers=_auth(admin_token),
            json={"role_ids": [manager_id]},
        )
        assert r.status_code == 200, r.text
        assert [row["name"] for row in r.json()["data"]] == ["manager"]
