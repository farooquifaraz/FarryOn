"""A broken CA-bundle env var must not take HTTPS down.

The PostgreSQL 18 Windows installer sets CURL_CA_BUNDLE to a file it never
ships. requests trusts that path blindly, so every HTTPS call in the process
raises OSError — which reached a user as "Couldn't reach Google to verify your
sign-in" on a phone that was working perfectly. These pin the sanitizer that
makes the backend survive it.
"""

from __future__ import annotations

import os

import pytest

from app.core.tls import sanitize_ca_env


_CA_VARS = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE")


@pytest.fixture(autouse=True)
def _clean_env():
    """Start each test from a known-empty slate, and put the machine's back.

    Clearing (not just restoring) matters: the developer machine that prompted
    all this *has* a broken CURL_CA_BUNDLE exported, so a test asserting "this
    dropped nothing" would otherwise see that ambient var and fail for a reason
    it isn't testing.
    """
    saved = {k: os.environ.get(k) for k in _CA_VARS}
    for k in _CA_VARS:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_drops_a_bundle_that_does_not_exist() -> None:
    os.environ["CURL_CA_BUNDLE"] = r"C:\Program Files\PostgreSQL\18\ssl\certs\ca-bundle.crt"
    assert sanitize_ca_env() == ["CURL_CA_BUNDLE"]
    assert "CURL_CA_BUNDLE" not in os.environ


def test_keeps_a_bundle_that_exists(tmp_path) -> None:
    # A real corporate proxy CA is a legitimate setup and none of our business.
    bundle = tmp_path / "corp-ca.crt"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)

    assert sanitize_ca_env() == []
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)


def test_drops_every_broken_var_and_leaves_the_good_one(tmp_path) -> None:
    bundle = tmp_path / "ok.crt"
    bundle.write_text("x")
    os.environ["CURL_CA_BUNDLE"] = str(tmp_path / "gone.crt")
    os.environ["SSL_CERT_FILE"] = str(tmp_path / "also-gone.crt")
    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)

    assert sorted(sanitize_ca_env()) == ["CURL_CA_BUNDLE", "SSL_CERT_FILE"]
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)


def test_ignores_unset_and_empty(tmp_path) -> None:
    # conftest blanks a lot of env vars to "" rather than popping them; an empty
    # value means "not set", not "a file named ''".
    os.environ.pop("CURL_CA_BUNDLE", None)
    os.environ["SSL_CERT_FILE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = "   "

    assert sanitize_ca_env() == []


def test_is_idempotent(tmp_path) -> None:
    os.environ["CURL_CA_BUNDLE"] = str(tmp_path / "nope.crt")
    assert sanitize_ca_env() == ["CURL_CA_BUNDLE"]
    assert sanitize_ca_env() == []


def test_creating_the_app_sanitizes_the_env() -> None:
    """The wiring, not just the helper: this is what the running server does."""
    from app.main import create_app

    os.environ["CURL_CA_BUNDLE"] = r"C:\nope\missing\ca-bundle.crt"
    create_app()
    assert "CURL_CA_BUNDLE" not in os.environ


def test_google_verification_reaches_google_despite_a_broken_var() -> None:
    """End-to-end proof, against real Google.

    A garbage token must come back as ValueError ("Wrong number of segments")
    — that is Google's *verifier* rejecting it, which can only happen once the
    signing certs were actually fetched. An OSError here means we never got out
    of the machine.
    """
    pytest.importorskip("google.auth")
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    os.environ["CURL_CA_BUNDLE"] = r"C:\Program Files\PostgreSQL\18\ssl\certs\ca-bundle.crt"
    sanitize_ca_env()

    try:
        google_id_token.verify_oauth2_token(
            "not.a.real.token", google_requests.Request(), "x.apps.googleusercontent.com"
        )
    except ValueError:
        pass  # reached Google, token rejected on its merits — the good path
    except OSError as exc:  # pragma: no cover - the bug this guards
        pytest.fail(f"broken CA env still breaks HTTPS: {exc}")
    except Exception as exc:  # noqa: BLE001 - offline CI, DNS, etc.
        pytest.skip(f"no network to Google: {type(exc).__name__}")
