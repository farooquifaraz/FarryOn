"""Append-only audit log: written by other modules' routers, read-only API."""

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


def test_audit_logs_require_permission() -> None:
    with _client() as client:
        r = client.get("/api/v1/audit-logs")
        assert r.status_code == 401

        asyncio.run(_seed_user_with_role("plain@example.com", "user"))
        token = _login(client, "plain@example.com")["access_token"]
        r = client.get("/api/v1/audit-logs", headers=_auth(token))
        assert r.status_code == 403


def test_register_and_login_are_logged() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root@example.com", "super_admin"))
        admin_token = _login(client, "root@example.com")["access_token"]

        client.post(
            "/api/v1/auth/register", json={"email": "a@example.com", "password": PASSWORD}
        )
        _login(client, "a@example.com")

        r = client.get("/api/v1/audit-logs", headers=_auth(admin_token))
        assert r.status_code == 200, r.text
        actions = [row["action"] for row in r.json()["data"]]
        assert "auth.register" in actions
        assert "auth.login" in actions


def test_filter_by_action() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root2@example.com", "super_admin"))
        admin_token = _login(client, "root2@example.com")["access_token"]

        client.post(
            "/api/v1/auth/register", json={"email": "b@example.com", "password": PASSWORD}
        )

        r = client.get(
            "/api/v1/audit-logs?action=auth.register", headers=_auth(admin_token)
        )
        rows = r.json()["data"]
        assert len(rows) >= 1
        assert all(row["action"] == "auth.register" for row in rows)


def test_role_change_is_logged_with_before_after() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root3@example.com", "super_admin"))
        admin_token = _login(client, "root3@example.com")["access_token"]

        r = client.post(
            "/api/v1/roles",
            headers=_auth(admin_token),
            json={"name": "auditor", "level": 15, "permission_codes": ["audit.read"]},
        )
        assert r.status_code == 200

        rows = client.get(
            "/api/v1/audit-logs?action=role.create", headers=_auth(admin_token)
        ).json()["data"]
        assert len(rows) == 1
        assert rows[0]["after"]["name"] == "auditor"
        assert rows[0]["actor_id"] is not None


def test_export_csv() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root4@example.com", "super_admin"))
        admin_token = _login(client, "root4@example.com")["access_token"]

        r = client.get("/api/v1/audit-logs/export", headers=_auth(admin_token))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert r.text.splitlines()[0].startswith("id,actor_id,impersonator_id,action")
