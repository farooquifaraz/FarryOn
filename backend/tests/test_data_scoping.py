"""Notes/tasks belong to the user who made them.

Everything here guards one property: two people using FarryOn must never see or
touch each other's rows. Until now every live session resolved to one shared
anonymous user, so this was vacuously true — one user, one pile. These tests are
what stop it regressing back to a single pile the moment the scoping in
main.py/repo.py loses a `user_id=` argument.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.db import repo
from app.db.base import get_sessionmaker
from app.main import create_app
from tests.test_ws_live import _handshake


def _client() -> TestClient:
    return TestClient(create_app())


def _sign_up(client: TestClient, email: str) -> str:
    """Register + log in, returning the access token."""
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-1"},
    )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct-horse-1"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user_id(client: TestClient, token: str) -> int:
    r = client.get("/api/v1/me", headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _seed_note(user_id: int, text: str) -> int:
    """Create a note owned by ``user_id``, as the agent's tools would."""

    async def _go() -> int:
        async with get_sessionmaker()() as db:
            note = await repo.add_note(db, text=text, user_id=user_id)
            await db.commit()
            return note.id

    return asyncio.run(_go())


def _seed_task(user_id: int, title: str) -> int:
    async def _go() -> int:
        async with get_sessionmaker()() as db:
            task = await repo.add_task(db, title=title, user_id=user_id)
            await db.commit()
            return task.id

    return asyncio.run(_go())


def _note_exists(note_id: int) -> bool:
    async def _go() -> bool:
        async with get_sessionmaker()() as db:
            return await db.get(repo.Note, note_id) is not None

    return asyncio.run(_go())


# ---- The live session's owner ----------------------------------------------


def test_ws_session_belongs_to_the_token_holder() -> None:
    """The other half of scoping: rows must be *created* owned, not just filtered.

    A WebSocket can't send an Authorization header, so the access token rides in
    `?token=`. This is the seam where identity used to be dropped on the floor —
    the session resolved every connection to the shared anonymous user, so no
    filter downstream could have told two people's notes apart.
    """
    with _client() as client:
        alice = _sign_up(client, "wsalice@example.com")
        alice_id = _user_id(client, alice)

        with client.websocket_connect(f"/ws/live?token={alice}") as ws:
            ready = _handshake(ws)
            session_id = ready["sessionId"]

        async def _owner_of_session() -> int | None:
            async with get_sessionmaker()() as db:
                row = await db.get(repo.Session, session_id)
                return row.user_id if row else None

        assert asyncio.run(_owner_of_session()) == alice_id


def test_ws_session_without_a_token_falls_back_to_anonymous() -> None:
    # Local runs connect with no token and must still work. Production closes
    # this door instead (settings.auth_enabled), which is not on in tests.
    with _client() as client:
        with client.websocket_connect("/ws/live") as ws:
            session_id = _handshake(ws)["sessionId"]

        async def _is_anon() -> bool:
            async with get_sessionmaker()() as db:
                row = await db.get(repo.Session, session_id)
                user = await db.get(repo.User, row.user_id)
                return user.external_id == repo.ANON_EXTERNAL_ID

        assert asyncio.run(_is_anon())


def test_ws_ignores_a_refresh_token_used_as_an_access_token() -> None:
    # The old hand-rolled check only verified the signature, so a refresh token
    # — which we sign with the same secret — passed as proof of identity.
    with _client() as client:
        _sign_up(client, "wsbob@example.com")
        r = client.post(
            "/api/v1/auth/login",
            json={"email": "wsbob@example.com", "password": "correct-horse-1"},
        )
        refresh = r.json()["data"]["refresh_token"]

        with client.websocket_connect(f"/ws/live?token={refresh}") as ws:
            session_id = _handshake(ws)["sessionId"]

        async def _is_anon() -> bool:
            async with get_sessionmaker()() as db:
                row = await db.get(repo.Session, session_id)
                user = await db.get(repo.User, row.user_id)
                return user.external_id == repo.ANON_EXTERNAL_ID

        assert asyncio.run(_is_anon()), "a refresh token must not name a user here"


# ---- Reads -----------------------------------------------------------------


def test_each_user_sees_only_their_own_notes() -> None:
    with _client() as client:
        alice = _sign_up(client, "alice@example.com")
        bob = _sign_up(client, "bob@example.com")
        _seed_note(_user_id(client, alice), "alice's secret")
        _seed_note(_user_id(client, bob), "bob's secret")

        r = client.get("/notes", headers=_auth(alice))
        assert r.status_code == 200
        assert [n["text"] for n in r.json()] == ["alice's secret"]

        r = client.get("/notes", headers=_auth(bob))
        assert [n["text"] for n in r.json()] == ["bob's secret"]


def test_each_user_sees_only_their_own_tasks() -> None:
    with _client() as client:
        alice = _sign_up(client, "alice2@example.com")
        bob = _sign_up(client, "bob2@example.com")
        _seed_task(_user_id(client, alice), "alice's task")
        _seed_task(_user_id(client, bob), "bob's task")

        r = client.get("/tasks", headers=_auth(alice))
        assert [t["title"] for t in r.json()] == ["alice's task"]


def test_signed_out_caller_sees_neither_users_data() -> None:
    # No header resolves to the anonymous user (auth is not enforced in tests,
    # matching a local run). That user owns nothing, so the reply is empty
    # rather than everybody's rows.
    with _client() as client:
        alice = _sign_up(client, "alice3@example.com")
        _seed_note(_user_id(client, alice), "alice's secret")

        assert client.get("/notes").json() == []


def test_expired_or_garbage_token_is_rejected_not_downgraded() -> None:
    # The dangerous failure is answering a bad token with the anonymous user's
    # data: the app would render someone else's rows instead of refreshing.
    with _client() as client:
        r = client.get("/notes", headers=_auth("not-a-real-jwt"))
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"


# ---- Writes ----------------------------------------------------------------


def test_user_cannot_delete_another_users_note() -> None:
    with _client() as client:
        alice = _sign_up(client, "alice4@example.com")
        bob = _sign_up(client, "bob4@example.com")
        note_id = _seed_note(_user_id(client, alice), "alice's secret")

        r = client.delete(f"/notes/{note_id}", headers=_auth(bob))
        assert r.status_code == 404, "not 403 — a 403 would confirm it exists"
        assert _note_exists(note_id), "bob's request must not have touched it"

        # ...and it is still Alice's to delete.
        assert client.delete(f"/notes/{note_id}", headers=_auth(alice)).status_code == 200
        assert not _note_exists(note_id)


def test_user_cannot_delete_another_users_task() -> None:
    with _client() as client:
        alice = _sign_up(client, "alice5@example.com")
        bob = _sign_up(client, "bob5@example.com")
        task_id = _seed_task(_user_id(client, alice), "alice's task")

        assert client.delete(f"/tasks/{task_id}", headers=_auth(bob)).status_code == 404
        assert client.get("/tasks", headers=_auth(alice)).json()[0]["id"] == task_id


def test_user_cannot_mark_another_users_task_done() -> None:
    with _client() as client:
        alice = _sign_up(client, "alice6@example.com")
        bob = _sign_up(client, "bob6@example.com")
        task_id = _seed_task(_user_id(client, alice), "alice's task")

        r = client.post(f"/tasks/{task_id}/done?done=true", headers=_auth(bob))
        assert r.status_code == 404
        assert client.get("/tasks", headers=_auth(alice)).json()[0]["done"] is False

        r = client.post(f"/tasks/{task_id}/done?done=true", headers=_auth(alice))
        assert r.status_code == 200
        assert r.json()["done"] is True
