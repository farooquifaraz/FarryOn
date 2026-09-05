"""Live translation draws on the SAME talk budget the assistant does.

It used to have a budget of its own, and that was the mistake: translation
runs through the same model at the same price, so a second allowance was a
second helping of the most expensive thing we sell — the old Plus plan sold
360 voice minutes and 1,350 translate minutes, and the translation nobody had
priced was the larger cost (repriced 2026-09-05). One budget also gives the
user one number to understand: minutes of talking, spent however they like.

`translate_seconds` is still written alongside, as a record of WHAT the
minutes went on for the admin view — never as a separate allowance.

Driven directly against ``Session``'s metering, for the reasons spelled out at
the top of ``test_voice_quota.py``: over a socket you would be testing the
harness's flush timing rather than the meter.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.db import repo
from app.db.base import get_sessionmaker
from app.ws.session import (
    _MIC_BYTES_PER_SECOND,
    _USAGE_RETRY_AFTER_S,
    Session,
)

_TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _session(user_id: int | None = 11) -> Session:
    """A translate-mode Session with only its metering state."""
    s = Session.__new__(Session)
    s.session_id = "test-translate"
    s._user_id = user_id
    s._plan_name = None
    s._mode = "translate"
    s._translate = {"target_language": "hi", "echo_target_language": False}
    s._voice_used_s = 0.0
    s._voice_pending_s = 0.0
    s._voice_capped = False
    s._translate_used_s = 0.0
    s._translate_pending_s = 0.0
    s._translate_capped = False
    s._translate_warned = False
    s._voice_flush_retry_at = 0.0
    s._translate_flush_retry_at = 0.0
    s._sent = []

    async def _send_error(code, message, fatal=False):  # noqa: ANN001
        s._sent.append((code, message, fatal))

    s._send_error = _send_error
    return s


@pytest.fixture
def caps():
    """Set both caps the way an operator would, and put them back after."""
    settings = get_settings()
    original = (settings.default_plan, settings.quota_enforcement_enabled)

    def _set(
        talk_seconds: int,
        *,
        enforcing: bool = True,
    ) -> None:
        # One budget: both meters read `voice_seconds`.
        settings.plan_limits["test-plan"] = {
            "voice_seconds": talk_seconds,
            "translate_seconds": talk_seconds,
        }
        object.__setattr__(settings, "default_plan", "test-plan")
        object.__setattr__(settings, "quota_enforcement_enabled", enforcing)

    yield _set

    object.__setattr__(settings, "default_plan", original[0])
    object.__setattr__(settings, "quota_enforcement_enabled", original[1])
    settings.plan_limits.pop("test-plan", None)


async def _row(key: str):
    async with get_sessionmaker()() as db:
        return await repo.get_daily_usage(db, user_key=key, day=_TODAY)


async def _translate_usage(key: str) -> int:
    row = await _row(key)
    return row.translate_seconds if row else 0


async def _voice_usage(key: str) -> int:
    row = await _row(key)
    return row.voice_seconds if row else 0


class TestTheSharedBudget:
    async def test_translation_spends_the_talk_budget(self, caps) -> None:
        caps(600)
        s = _session()

        assert await s._meter_translate(_MIC_BYTES_PER_SECOND * 4) is True
        await s._flush_translate_usage()

        assert await _voice_usage("u11") == 4, (
            "translation must draw down the one talk budget"
        )
        assert await _translate_usage("u11") == 4, (
            "and still be recorded as translation, for the admin view"
        )

    async def test_a_spent_budget_stops_translation_too(self, caps) -> None:
        # The whole point of one budget: talking to Farry all month leaves
        # nothing to translate with, and the user is told, not surprised.
        caps(10)
        s = _session()
        s._translate_used_s = 9_999

        assert await s._meter_translate(_MIC_BYTES_PER_SECOND * 5) is False
        assert s._translate_capped is True

    async def test_translation_then_speech_share_one_wallet(self, caps) -> None:
        caps(20)
        s = _session()
        await s._meter_translate(_MIC_BYTES_PER_SECOND * 25)
        assert s._translate_capped is True

        # The assistant meter is a separate counter in memory, but it bills
        # the same column — what one spends, the other no longer has.
        assert await _voice_usage("u11") >= 25


class TestTheCap:
    async def test_going_over_ends_the_session_with_a_reason(self, caps) -> None:
        caps(120)
        s = _session()

        assert await s._meter_translate(_MIC_BYTES_PER_SECOND * 121) is False
        code, message, fatal = s._sent[-1]
        assert code == "quota_exceeded"
        assert fatal is True
        assert "2 minutes" in message
        assert "translation" in message.lower()

    async def test_everything_after_the_cap_is_refused(self, caps) -> None:
        caps(10)
        s = _session()
        await s._meter_translate(_MIC_BYTES_PER_SECOND * 11)
        assert await s._meter_translate(_MIC_BYTES_PER_SECOND) is False

    async def test_the_seconds_up_to_the_cap_are_still_billed(
        self, caps
    ) -> None:
        # Being cut off is not a refund.
        caps(10)
        s = _session()
        await s._meter_translate(_MIC_BYTES_PER_SECOND * 11)
        assert await _voice_usage("u11") == 11

    async def test_a_negative_cap_means_unlimited(self, caps) -> None:
        caps(-1)
        s = _session()
        assert await s._meter_translate(_MIC_BYTES_PER_SECOND * 10_000) is True
        assert s._translate_capped is False

    async def test_enforcement_off_still_counts_but_never_refuses(
        self, caps
    ) -> None:
        # Usage data stays honest even when the cap is not being applied.
        caps(1, enforcing=False)
        s = _session()
        assert await s._meter_translate(_MIC_BYTES_PER_SECOND * 20) is True
        await s._flush_translate_usage()
        assert await _translate_usage("u11") == 20


class TestTheWarning:
    async def test_one_heads_up_before_the_wall(self, caps) -> None:
        # Being cut off mid-meeting without notice is worse than the
        # interruption of being told it is coming.
        caps(100)
        s = _session()
        await s._meter_translate(_MIC_BYTES_PER_SECOND * 85)

        assert [c for c, _, _ in s._sent] == ["quota_warning"]
        assert s._sent[0][2] is False, "a warning must not end the session"

    async def test_the_warning_fires_once_not_per_frame(self, caps) -> None:
        caps(100)
        s = _session()
        for _ in range(10):
            await s._meter_translate(_MIC_BYTES_PER_SECOND)  # 10s
        for _ in range(80):
            await s._meter_translate(_MIC_BYTES_PER_SECOND)  # past 80%

        warnings = [c for c, _, _ in s._sent if c == "quota_warning"]
        assert len(warnings) == 1

    async def test_no_warning_when_well_under(self, caps) -> None:
        caps(600)
        s = _session()
        await s._meter_translate(_MIC_BYTES_PER_SECOND * 60)
        assert s._sent == []


class TestFailureModes:
    async def test_a_database_failure_lets_the_audio_through(
        self, caps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Metering protects the operator's bill. Cutting someone off
        # mid-sentence over a DB hiccup costs more than the seconds it saves,
        # and under-billing is recoverable where a dead conversation is not.
        caps(600)
        s = _session()

        async def _boom(*a, **k):  # noqa: ANN001, ANN002, ANN003
            raise RuntimeError("db is down")

        monkeypatch.setattr(repo, "bump_daily_usage", _boom)

        for _ in range(20):  # crosses the flush threshold
            assert await s._meter_translate(_MIC_BYTES_PER_SECOND) is True
        # The seconds stay pending so a later flush still bills them.
        assert s._translate_pending_s >= 15

    async def test_short_sessions_are_billed_on_teardown(self, caps) -> None:
        # Without a flush at close, every session under the batch window would
        # be free — translate all day in 14-second bursts and pay nothing.
        caps(600)
        s = _session()
        await s._meter_translate(_MIC_BYTES_PER_SECOND * 4)
        assert await _translate_usage("u11") == 0, "must not write per frame"

        await s._flush_translate_usage()
        assert await _translate_usage("u11") == 4

class TestAFailingDatabaseIsNotHammered:
    """Device-found, 2026-08-10.

    A failed flush keeps its seconds pending on purpose, so the next flush
    still bills them. But pending stays ABOVE the flush threshold, so without a
    backoff the very next audio frame retries — and the next, and the next. One
    real translate session against a database missing a column produced **2021
    failed queries**, about fifty a second, for as long as it ran. SQLite
    shrugged; Postgres would not have.
    """

    async def test_a_failed_write_is_not_retried_on_the_next_frame(
        self, caps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        caps(600)
        s = _session()
        attempts = 0

        async def _boom(*a, **k):  # noqa: ANN001, ANN002, ANN003
            nonlocal attempts
            attempts += 1
            raise RuntimeError("no such column")

        monkeypatch.setattr(repo, "bump_daily_usage", _boom)

        # 60 seconds of audio: four flush thresholds' worth.
        for _ in range(60):
            await s._meter_translate(_MIC_BYTES_PER_SECOND)

        assert attempts == 1, (
            f"{attempts} database round-trips for one outage — the backoff is "
            "not holding"
        )
        assert s._translate_pending_s >= 15, "the seconds must stay billable"

    async def test_it_recovers_once_the_database_does(
        self, caps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        caps(600)
        s = _session()
        broken = True

        original = repo.bump_daily_usage

        async def _flaky(*a, **k):  # noqa: ANN001, ANN002, ANN003
            if broken:
                raise RuntimeError("down")
            return await original(*a, **k)

        monkeypatch.setattr(repo, "bump_daily_usage", _flaky)

        for _ in range(20):
            await s._meter_translate(_MIC_BYTES_PER_SECOND)
        assert await _translate_usage("u11") == 0

        broken = False
        # Pretend the backoff has elapsed rather than sleeping 30 s.
        s._translate_flush_retry_at -= _USAGE_RETRY_AFTER_S + 1
        await s._flush_translate_usage()

        assert await _translate_usage("u11") == 20, (
            "the seconds withheld during the outage were never billed"
        )
        assert s._translate_flush_retry_at == 0.0
