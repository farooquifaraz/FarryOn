"""Admin-side user management: list/search, invite, update, soft delete,
bulk actions, CSV export, and the guard rails shared with RBAC (self-action
block, role-level comparison, last-super-admin protection).
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


def _login(client: TestClient, email: str) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _role_id(name: str) -> int:
    async def _get() -> int:
        sessionmaker = db_base.get_sessionmaker()
        async with sessionmaker() as db:
            role = (await db.execute(select(Role).where(Role.name == name))).scalar_one()
            return role.id

    return asyncio.run(_get())


def test_list_users_requires_permission() -> None:
    with _client() as client:
        r = client.get("/api/v1/users")
        assert r.status_code == 401

        asyncio.run(_seed_user_with_role("plain@example.com", "user"))
        token = _login(client, "plain@example.com")
        r = client.get("/api/v1/users", headers=_auth(token))
        assert r.status_code == 403


def test_list_users_search_and_pagination_meta() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root@example.com", "super_admin"))
        token = _login(client, "root@example.com")

        client.post(
            "/api/v1/auth/register",
            json={"email": "alice@example.com", "password": PASSWORD, "display_name": "Alice"},
        )
        client.post(
            "/api/v1/auth/register",
            json={"email": "bob@example.com", "password": PASSWORD, "display_name": "Bob"},
        )

        r = client.get("/api/v1/users", headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["meta"]["total"] == 3  # root + alice + bob
        assert r.json()["meta"]["page"] == 1

        r = client.get("/api/v1/users?search=alice", headers=_auth(token))
        emails = [u["email"] for u in r.json()["data"]]
        assert emails == ["alice@example.com"]


def test_invite_flow_status_transitions_invited_to_active() -> None:
    import app.modules.auth.notifications as notifications

    captured: dict[str, str] = {}
    original = notifications.send_invite_email

    def _capture(*, to_email: str, token: str) -> None:
        captured["token"] = token

    notifications.send_invite_email = _capture
    try:
        with _client() as client:
            asyncio.run(_seed_user_with_role("root2@example.com", "super_admin"))
            token = _login(client, "root2@example.com")

            r = client.post(
                "/api/v1/users",
                headers=_auth(token),
                json={"email": "invitee@example.com", "display_name": "Invitee", "role_ids": []},
            )
            assert r.status_code == 200, r.text
            assert r.json()["data"]["status"] == "invited"
            assert "token" in captured

            # Can't log in yet — no password set.
            r = client.post(
                "/api/v1/auth/login",
                json={"email": "invitee@example.com", "password": "anything"},
            )
            assert r.status_code == 401

            r = client.post(
                "/api/v1/auth/reset-password",
                json={"token": captured["token"], "new_password": "new-correct-horse-2"},
            )
            assert r.status_code == 200, r.text

            r = client.post(
                "/api/v1/auth/login",
                json={"email": "invitee@example.com", "password": "new-correct-horse-2"},
            )
            assert r.status_code == 200
    finally:
        notifications.send_invite_email = original


def test_invite_duplicate_email_rejected() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root3@example.com", "super_admin"))
        token = _login(client, "root3@example.com")

        client.post(
            "/api/v1/auth/register",
            json={"email": "taken@example.com", "password": PASSWORD},
        )
        r = client.post(
            "/api/v1/users", headers=_auth(token), json={"email": "taken@example.com"}
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "EMAIL_TAKEN"


def test_update_status_to_suspended_force_logs_out() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root4@example.com", "super_admin"))
        admin_token = _login(client, "root4@example.com")

        client.post(
            "/api/v1/auth/register", json={"email": "target@example.com", "password": PASSWORD}
        )
        target_login = client.post(
            "/api/v1/auth/login", json={"email": "target@example.com", "password": PASSWORD}
        ).json()["data"]
        target_id = client.get(
            "/api/v1/me", headers=_auth(target_login["access_token"])
        ).json()["data"]["id"]

        r = client.patch(
            f"/api/v1/users/{target_id}",
            headers=_auth(admin_token),
            json={"status": "suspended"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "suspended"

        # Old access token is now dead.
        r2 = client.get(
            "/api/v1/me", headers=_auth(target_login["access_token"])
        )
        assert r2.status_code == 401

        # And the suspended account can't log back in.
        r3 = client.post(
            "/api/v1/auth/login", json={"email": "target@example.com", "password": PASSWORD}
        )
        assert r3.status_code == 403
        assert r3.json()["error"]["code"] == "USER_SUSPENDED"


def test_soft_delete_frees_email_for_reuse() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root5@example.com", "super_admin"))
        admin_token = _login(client, "root5@example.com")

        client.post(
            "/api/v1/auth/register", json={"email": "leaving@example.com", "password": PASSWORD}
        )
        target_id = client.get(
            "/api/v1/me",
            headers=_auth(_login(client, "leaving@example.com")),
        ).json()["data"]["id"]

        r = client.delete(f"/api/v1/users/{target_id}", headers=_auth(admin_token))
        assert r.status_code == 200

        # No longer visible in the list...
        r2 = client.get("/api/v1/users?search=leaving", headers=_auth(admin_token))
        assert r2.json()["data"] == []

        # ...and the email can be claimed again.
        r3 = client.post(
            "/api/v1/auth/register",
            json={"email": "leaving@example.com", "password": "another-pass-2"},
        )
        assert r3.status_code == 200, r3.text


def test_bulk_action_partial_success() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root6@example.com", "super_admin"))
        admin_token = _login(client, "root6@example.com")

        client.post(
            "/api/v1/auth/register", json={"email": "u1@example.com", "password": PASSWORD}
        )
        u1_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "u1@example.com"))
        ).json()["data"]["id"]

        r = client.post(
            "/api/v1/users/bulk",
            headers=_auth(admin_token),
            json={"user_ids": [u1_id, 999999], "action": "suspend"},
        )
        assert r.status_code == 200, r.text
        results = {row["user_id"]: row for row in r.json()["data"]}
        assert results[u1_id]["ok"] is True
        assert results[999999]["ok"] is False
        assert results[999999]["error"] == "NOT_FOUND"


def test_admin_cannot_act_on_self_or_higher_level_user() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("selfadmin@example.com", "admin"))
        asyncio.run(_seed_user_with_role("root7@example.com", "super_admin"))
        admin_token = _login(client, "selfadmin@example.com")

        self_id = client.get("/api/v1/me", headers=_auth(admin_token)).json()["data"]["id"]
        root_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "root7@example.com"))
        ).json()["data"]["id"]

        r = client.patch(
            f"/api/v1/users/{self_id}", headers=_auth(admin_token), json={"display_name": "x"}
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SELF_ACTION_FORBIDDEN"

        r = client.patch(
            f"/api/v1/users/{root_id}", headers=_auth(admin_token), json={"display_name": "x"}
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INSUFFICIENT_ROLE_LEVEL"


def test_second_super_admin_can_be_suspended_leaving_one() -> None:
    """Two super_admins: suspending one is allowed (the other remains) — the
    "not the last one" half of the LAST_SUPER_ADMIN guard.
    """
    with _client() as client:
        asyncio.run(_seed_user_with_role("root8@example.com", "super_admin"))
        asyncio.run(_seed_user_with_role("root9@example.com", "super_admin"))
        actor_token = _login(client, "root8@example.com")
        target_id = client.get(
            "/api/v1/me", headers=_auth(_login(client, "root9@example.com"))
        ).json()["data"]["id"]

        r = client.patch(
            f"/api/v1/users/{target_id}", headers=_auth(actor_token), json={"status": "suspended"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "suspended"


def test_is_last_super_admin_helper() -> None:
    """The LAST_SUPER_ADMIN guard itself (app.modules.rbac.service.is_last_super_admin).

    Not reachable end-to-end through this API in the current hierarchy: any
    actor who can legitimately touch a super_admin target must themselves
    hold super_admin (self-action is separately blocked), so a *second*
    super_admin always exists at the moment the check would matter — the
    guard is defense-in-depth for that invariant ever changing, e.g. a
    future internal/service-level caller. Verified directly here instead.
    """
    from app.modules.rbac.service import is_last_super_admin

    async def _run() -> None:
        await _seed_user_with_role("solo@example.com", "super_admin")
        sessionmaker = db_base.get_sessionmaker()
        async with sessionmaker() as db:
            solo = (
                await db.execute(select(User).where(User.email == "solo@example.com"))
            ).scalar_one()
            assert await is_last_super_admin(db, solo.id) is True

        await _seed_user_with_role("second@example.com", "super_admin")
        async with sessionmaker() as db:
            solo = (
                await db.execute(select(User).where(User.email == "solo@example.com"))
            ).scalar_one()
            assert await is_last_super_admin(db, solo.id) is False

    asyncio.run(_run())


def test_export_users_csv() -> None:
    with _client() as client:
        asyncio.run(_seed_user_with_role("root10@example.com", "super_admin"))
        token = _login(client, "root10@example.com")

        r = client.get("/api/v1/users/export", headers=_auth(token))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "root10@example.com" in r.text
        assert r.text.splitlines()[0] == "id,email,display_name,status,roles,created_at"
