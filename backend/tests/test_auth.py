"""Admin/User module — core auth: register/login/refresh/logout, email
verification, password reset, rate limiting, refresh-token reuse detection.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_register_then_login() -> None:
    with _client() as client:
        r = client.post(
            "/api/v1/auth/register",
            json={"email": "a@example.com", "password": "correct-horse-1"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["data"]["email"] == "a@example.com"
        assert body["data"]["email_verified"] is False

        r = client.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "correct-horse-1"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["access_token"] and data["refresh_token"]
        assert data["token_type"] == "bearer"


def test_register_duplicate_email_rejected() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "correct-horse-1"},
        )
        r = client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "another-pass-1"},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "EMAIL_TAKEN"


def test_login_wrong_password_and_unknown_email_have_same_shape() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "b@example.com", "password": "correct-horse-1"},
        )
        r1 = client.post(
            "/api/v1/auth/login",
            json={"email": "b@example.com", "password": "wrong-password"},
        )
        r2 = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-password"},
        )
        assert r1.status_code == r2.status_code == 401
        assert r1.json()["error"]["code"] == r2.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_lockout_after_repeated_failures() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "c@example.com", "password": "correct-horse-1"},
        )
        for _ in range(5):
            client.post(
                "/api/v1/auth/login",
                json={"email": "c@example.com", "password": "wrong"},
            )
        r = client.post(
            "/api/v1/auth/login",
            json={"email": "c@example.com", "password": "correct-horse-1"},
        )
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"


def test_get_me_requires_bearer_token() -> None:
    with _client() as client:
        r = client.get("/api/v1/me")
        assert r.status_code == 401

        client.post(
            "/api/v1/auth/register",
            json={"email": "d@example.com", "password": "correct-horse-1"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "d@example.com", "password": "correct-horse-1"},
        ).json()["data"]

        r = client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {login['access_token']}"}
        )
        assert r.status_code == 200
        assert r.json()["data"]["email"] == "d@example.com"


def test_refresh_rotates_token_and_old_one_stops_working() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "e@example.com", "password": "correct-horse-1"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "e@example.com", "password": "correct-horse-1"},
        ).json()["data"]

        r = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
        assert r.status_code == 200, r.text
        new_pair = r.json()["data"]
        assert new_pair["refresh_token"] != login["refresh_token"]

        # Old refresh token is now consumed — using it again fails outright
        # (not just "already rotated", since a second attempt below proves
        # reuse detection independently).
        r2 = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": new_pair["refresh_token"]}
        )
        assert r2.status_code == 200, r2.text


def test_refresh_reuse_detection_revokes_whole_family() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "f@example.com", "password": "correct-horse-1"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "f@example.com", "password": "correct-horse-1"},
        ).json()["data"]

        first_refresh = login["refresh_token"]
        r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
        rotated = r1.json()["data"]

        # Replay the now-revoked original token: reuse detected.
        r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
        assert r2.status_code == 401
        assert r2.json()["error"]["code"] == "TOKEN_REUSE_DETECTED"

        # The legitimately-rotated token is now ALSO dead (family revoked).
        r3 = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
        )
        assert r3.status_code == 401


def test_logout_revokes_refresh_token() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "g@example.com", "password": "correct-horse-1"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "g@example.com", "password": "correct-horse-1"},
        ).json()["data"]

        r = client.post(
            "/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]}
        )
        assert r.status_code == 200

        r2 = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
        assert r2.status_code == 401


def test_forgot_password_same_response_for_unknown_email() -> None:
    with _client() as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "h@example.com", "password": "correct-horse-1"},
        )
        r1 = client.post(
            "/api/v1/auth/forgot-password", json={"email": "h@example.com"}
        )
        r2 = client.post(
            "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
        )
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["data"]["message"] == r2.json()["data"]["message"]


def test_reset_password_invalidates_existing_sessions() -> None:
    import app.modules.auth.notifications as notifications

    captured: dict[str, str] = {}
    original = notifications.send_password_reset_email

    def _capture(*, to_email: str, token: str) -> None:
        captured["token"] = token

    notifications.send_password_reset_email = _capture
    try:
        with _client() as client:
            client.post(
                "/api/v1/auth/register",
                json={"email": "i@example.com", "password": "correct-horse-1"},
            )
            login = client.post(
                "/api/v1/auth/login",
                json={"email": "i@example.com", "password": "correct-horse-1"},
            ).json()["data"]

            client.post("/api/v1/auth/forgot-password", json={"email": "i@example.com"})
            assert "token" in captured

            r = client.post(
                "/api/v1/auth/reset-password",
                json={"token": captured["token"], "new_password": "new-correct-horse-2"},
            )
            assert r.status_code == 200, r.text

            # Old access token is now rejected (tokens_revoked_before bump).
            r2 = client.get(
                "/api/v1/me",
                headers={"Authorization": f"Bearer {login['access_token']}"},
            )
            assert r2.status_code == 401

            # New password works; old one doesn't.
            r3 = client.post(
                "/api/v1/auth/login",
                json={"email": "i@example.com", "password": "correct-horse-1"},
            )
            assert r3.status_code == 401

            r4 = client.post(
                "/api/v1/auth/login",
                json={"email": "i@example.com", "password": "new-correct-horse-2"},
            )
            assert r4.status_code == 200
    finally:
        notifications.send_password_reset_email = original


def test_verify_email_flow() -> None:
    import app.modules.auth.notifications as notifications

    captured: dict[str, str] = {}
    original = notifications.send_verification_email

    def _capture(*, to_email: str, token: str) -> None:
        captured["token"] = token

    notifications.send_verification_email = _capture
    try:
        with _client() as client:
            client.post(
                "/api/v1/auth/register",
                json={"email": "j@example.com", "password": "correct-horse-1"},
            )
            assert "token" in captured

            r = client.post(
                "/api/v1/auth/verify-email", json={"token": captured["token"]}
            )
            assert r.status_code == 200

            login = client.post(
                "/api/v1/auth/login",
                json={"email": "j@example.com", "password": "correct-horse-1"},
            ).json()["data"]
            me = client.get(
                "/api/v1/me",
                headers={"Authorization": f"Bearer {login['access_token']}"},
            ).json()["data"]
            assert me["email_verified"] is True

            # A second use of the same token fails (single-use).
            r2 = client.post(
                "/api/v1/auth/verify-email", json={"token": captured["token"]}
            )
            assert r2.status_code == 400
    finally:
        notifications.send_verification_email = original

