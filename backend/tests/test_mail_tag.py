"""Mail that does not come from the live site says so in its subject.

A developer box carried the live SMTP login, and order / status mails from
local runs landed in Faraz's inbox looking exactly like real ones
(2026-09-24). The live site sends plain subjects; everything else is tagged.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.modules.auth import notifications
from app.modules.auth.notifications import LOCAL_TAG, subject_tag


def _s(base: str, tag: str = "") -> SimpleNamespace:
    return SimpleNamespace(sso_redirect_base_url=base, email_subject_tag=tag)


@pytest.mark.parametrize(
    "base",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://192.168.1.107:8000",
        "https://localhost",
        "http://farryon.izylrn.com",  # not https: not the live site
        "",
    ],
)
def test_anything_but_the_live_site_is_tagged(base: str) -> None:
    assert subject_tag(_s(base)) == LOCAL_TAG


def test_the_live_site_sends_plain_subjects() -> None:
    assert subject_tag(_s("https://farryon.izylrn.com")) == ""


def test_an_explicit_tag_wins() -> None:
    assert subject_tag(_s("https://farryon.izylrn.com", "[STAGING]")) == "[STAGING]"


def test_the_tag_reaches_the_subject_line(monkeypatch) -> None:
    sent: list = []

    class _SMTP:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

        def login(self, *a) -> None:
            return None

        def send_message(self, msg) -> None:
            sent.append(msg)

    class _Thread:
        def __init__(self, target, **k) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    settings = SimpleNamespace(
        auth_smtp_host="smtp.example.com",
        auth_smtp_port=465,
        auth_smtp_user="u@example.com",
        auth_smtp_password="x",
        auth_email_from="",
        auth_email_from_name="FarryOn",
        sso_redirect_base_url="http://localhost:8000",
        email_subject_tag="",
    )
    monkeypatch.setattr(notifications, "get_settings", lambda: settings)
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", _SMTP)
    monkeypatch.setattr(notifications.threading, "Thread", _Thread)
    notifications.send_operator_mail(
        to_email="ops@example.com", subject="Order #1 marked shipped", text="t", html="h"
    )
    assert [m["Subject"] for m in sent] == [f"{LOCAL_TAG} Order #1 marked shipped"]


def test_the_suite_never_holds_a_real_smtp_login() -> None:
    # conftest blanks these, so no test run can mail a real inbox.
    for key in ("AUTH_SMTP_HOST", "AUTH_SMTP_USER", "AUTH_SMTP_PASSWORD"):
        assert os.environ.get(key) == ""
