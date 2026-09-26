"""An app too old to serve is told to update — including copies already out.

The app is sideloaded (no Play Store), so nothing can update it silently
(Faraz, 2026-09-26: "can we send hard push update"). The server refuses a
build below ``min_app_build`` at hello, before any provider is connected:
a build that knows ``update_required`` gets that code (its Download screen),
an older one gets ``provider_unavailable`` — the fatal code it already shows
as a notice with a manual Retry, no reconnect loop. The website's
/download/info tells the app the minimum and the latest build.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.core.app_version import base_build, is_outdated
from app.main import create_app


@pytest.fixture(autouse=True)
def _dev_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "jwt_secret", "dev-insecure-change-me")


# ---- which build is it -----------------------------------------------------------

@pytest.mark.parametrize(
    "version, abi, expected",
    [
        ("1.0.0+4531", None, 2531),      # arm64 split, no ABI sent (old builds)
        ("1.0.0+3531", None, 2531),      # arm32 split
        ("1.0.0+6531", None, 2531),      # x86_64 split
        ("1.0.0+4531", "arm64", 2531),   # the ABI, when sent, decides
        ("1.0.0+3531", "arm32", 2531),
        ("1.0.0+1", None, 1),            # a debug build: no split
        ("1.0.0", None, None),           # no build number: not judged
        ("1.0.0+abc", None, None),
        (None, None, None),
    ],
)
def test_the_base_build_is_recovered_from_the_version_code(version, abi, expected) -> None:
    assert base_build(version, abi) == expected


def test_who_is_outdated() -> None:
    assert is_outdated("1.0.0+4531", None, "android", 2540) is True
    assert is_outdated("1.0.0+4540", None, "android", 2540) is False
    assert is_outdated("1.0.0+4531", None, "android", 0) is False, "0 = off"
    assert is_outdated("1.0.0+4531", None, "ios", 2540) is False, "Android only"
    assert is_outdated("1.0.0", None, "android", 2540) is False, "no build: let through"


# ---- the handshake refuses ---------------------------------------------------------

def _hello(app_version: str, features: list[str] | None = None) -> dict:
    client: dict = {"platform": "android", "appVersion": app_version}
    if features is not None:
        client["features"] = features
    return {
        "type": "hello",
        "protocolVersion": 1,
        "client": client,
        "device": {"kind": "phone", "id": "dev-1", "capabilities": ["audio_in"]},
        "session": {},
    }


def _first_message(hello: dict) -> dict:
    return _messages(hello, 1)[0]


def _messages(hello: dict, n: int) -> list[dict]:
    client = TestClient(create_app())
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json(hello)
        return [ws.receive_json() for _ in range(n)]


def test_an_old_build_is_refused_with_a_code_it_already_shows(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "min_app_build", 2540)
    monkeypatch.setattr(get_settings(), "app_download_url", "https://farryon.izylrn.com/download")
    words, stop = _messages(_hello("1.0.0+4531"), 2)
    # First the words, as a non-fatal error: an old build shows its text as a
    # banner (its fatal-outage screen has fixed words of its own — device
    # 2026-09-27, Vivo 4438 read "Service temporarily unavailable").
    assert words["type"] == "error" and words["fatal"] is False
    assert words["code"] == "update_required"
    assert "new version of FarryOn is required" in words["message"]
    assert "https://farryon.izylrn.com/download" in words["message"]
    # Then the stop: the fatal code an old build ends on without a reconnect loop.
    assert stop["type"] == "error" and stop["fatal"] is True
    assert stop["code"] == "provider_unavailable"


def test_a_build_that_knows_the_code_gets_update_required(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "min_app_build", 2540)
    msg = _first_message(_hello("1.0.0+4531", features=["update_required"]))
    assert msg["type"] == "error" and msg["code"] == "update_required" and msg["fatal"] is True


def test_a_current_build_is_served(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "min_app_build", 2540)
    msg = _first_message(_hello("1.0.0+4540", features=["update_required"]))
    assert msg["type"] == "ready"


def test_no_minimum_serves_everyone(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "min_app_build", 0)
    assert _first_message(_hello("1.0.0+4001"))["type"] == "ready"


# ---- the website tells the app ------------------------------------------------------

def test_download_info_carries_the_minimum(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "min_app_build", 2540)
    info = TestClient(create_app()).get("/download/info").json()
    assert info["minBuild"] == 2540
    monkeypatch.setattr(get_settings(), "min_app_build", 0)
    assert TestClient(create_app()).get("/download/info").json()["minBuild"] == 0
