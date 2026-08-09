"""Live translation is metered on its OWN budget, never the assistant's.

The point of a second meter is that one product cannot spend the other's
allowance. Translating a half-hour meeting must not leave someone unable to
speak to Farry, and a long conversation with Farry must not eat the translation
they were about to need in a taxi.

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
from app.ws.session import _MIC_BYTES_PER_SECOND, Session

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
        translate_seconds: int,
        *,
        voice_seconds: int = 6000,
        enforcing: bool = True,
    ) -> None:
        settings.plan_limits["test-plan"] = {
            "voice_seconds": voice_seconds,
            "translate_seconds": translate_seconds,
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


class TestTheTwoBudgetsAreSeparate:
    async def test_translation_is_written_to_its_own_column(self, caps) -> None:
        caps(600)
        s = _session()

        assert await s._meter_translate(_MIC_BYTES_PER_SECOND * 4) is True
        await s._flush_translate_usage()

        assert await _translate_usage("u11") == 4
        assert await _voice_usage("u11") == 0, (
            "translation must never be charged to the assistant's allowance"
        )

    async def test_a_spent_voice_budget_does_not_block_translation(
        self, caps
    ) -> None:
        # Someone who has talked to Farry all day can still be translated for.
        caps(600, voice_seconds=1)
        s = _session()
        s._voice_used_s = 9_999
        s._voice_capped = True

        assert await s._meter_translate(_MIC_BYTES_PER_SECOND * 5) is True
        assert s._translate_capped is False

    async def test_a_spent_translate_budget_does_not_block_the_assistant(
        self, caps
    ) -> None:
        caps(1, voice_seconds=600)
        s = _session()
        await s._meter_translate(_MIC_BYTES_PER_SECOND * 30)
        assert s._translate_capped is True

        # The voice meter is untouched and still lets speech through.
        assert await s._meter_voice(_MIC_BYTES_PER_SECOND * 2) is True


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
        assert await _translate_usage("u11") == 11

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
