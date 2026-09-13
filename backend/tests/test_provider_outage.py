"""A model that will not connect is reported in words, not exception reprs.

Until 2026-09-13 a depleted Gemini prepaid balance reached the app as
``provider_unavailable`` + the raw repr, and the app reconnected into the
same failure forever ("connecting" spinner). Now the cause is classified,
the message is for a person, and the operator is mailed (rate-limited).
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.ws import session as session_mod
from app.ws.session import Session, classify_provider_failure


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (RuntimeError("APIError('1011 None. Your prepayment credits are depleted.')"), "provider_credits"),
        (RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"), "provider_credits"),
        (RuntimeError("no usable Gemini Live model for this key (discovered=[...]) last error: 1011"), "provider_credits"),
        (RuntimeError("GEMINI_API_KEY is not set."), "provider_unavailable"),
        (ConnectionError("dns lookup failed"), "provider_unavailable"),
    ],
)
def test_failures_are_classified(exc, code) -> None:
    got, message = classify_provider_failure(exc)
    assert got == code
    assert "Farry" in message and "APIError" not in message


@pytest.mark.asyncio
async def test_report_sends_a_plain_fatal_error_and_one_alert(monkeypatch) -> None:
    sent: list[dict] = []
    mails: list[dict] = []

    async def fake_send_json(payload):
        sent.append(payload)

    def fake_alert(**kw):
        mails.append(kw)

    monkeypatch.setattr(session_mod, "_OUTAGE_ALERTED_AT", 0.0)
    import app.modules.auth.notifications as notif

    monkeypatch.setattr(notif, "send_outage_alert", fake_alert)

    s = Session(
        object(),
        gateway_factory=lambda *a: None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        settings=Settings(refine_user_transcripts=False, first_super_admin_email="ops@example.com"),
    )
    s._send_json = fake_send_json  # type: ignore[method-assign]
    exc = RuntimeError("APIError('1011 None. Your prepayment credits are depleted.')")
    await s._report_provider_failure(exc)
    await s._report_provider_failure(exc)  # a second failure in the same outage

    assert [p["code"] for p in sent] == ["provider_credits", "provider_credits"]
    assert all(p["fatal"] for p in sent)
    assert "depleted" not in sent[0]["message"]
    assert len(mails) == 1, "one alert per outage window, not one per session"
    assert mails[0]["to_email"] == "ops@example.com"
    assert "provider_credits" in mails[0]["subject"]
