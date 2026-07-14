"""Phase 9 hardening: security headers and request-id correlation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_security_headers_on_api_responses() -> None:
    with _client() as client:
        r = client.get("/healthz")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert "max-age" in r.headers["Strict-Transport-Security"]
        assert r.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"


def test_docs_exempt_from_csp() -> None:
    with _client() as client:
        r = client.get("/docs")
        assert r.status_code == 200
        # Swagger UI loads its assets from a CDN — a strict CSP would blank it.
        assert "Content-Security-Policy" not in r.headers
        # But the other headers still apply.
        assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_request_id_generated_and_echoed() -> None:
    with _client() as client:
        r = client.get("/healthz")
        assert len(r.headers["X-Request-ID"]) == 16


def test_inbound_request_id_is_honored() -> None:
    with _client() as client:
        r = client.get("/healthz", headers={"X-Request-ID": "proxy-abc-123"})
        assert r.headers["X-Request-ID"] == "proxy-abc-123"
