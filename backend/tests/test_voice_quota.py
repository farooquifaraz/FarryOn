"""Mic audio is metered and capped.

It wasn't. `config.py` has sold `voice_seconds` caps (free 300, pro 900) since
the plans were written, but nothing ever called `check_quota` for them — only
`image_scans` and `web_searches` did — so `daily_usage.voice_seconds` sat at 0
after a real spoken session and the cap could never fire. Voice is the most
expensive thing FarryOn does, so it was the one resource whose limit meant
nothing.

Found by working through docs/TEST_PLAN.md and asking why voice_seconds was 0
after Faraz actually spoke to it (D1).

These drive `Session`'s metering directly rather than over a WebSocket. The
socket brings a mock gateway that answers audio with audio, a batched flush, and
a teardown the TestClient doesn't wait for — three timing problems that test the
harness rather than the meter. The single line joining the two lives in
`_handle_binary`:

    if not await self._meter_voice(len(payload)):
        return
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.db import repo
from app.db.base import get_sessionmaker
from app.ws.session import _MIC_BYTES_PER_SECOND, Session

_TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _session(user_id: int | None = 7) -> Session:
    """A Session with only its metering state — no socket, no gateway."""
    s = Session.__new__(Session)
    s.session_id = "test-session"
    s._user_id = user_id
    # None means "no resolved plan", so plan_cap() falls back to default_plan —
    # which is what cap_of() sets. A real session resolves this in
    # _load_voice_usage; this fixture skips that to test metering in isolation.
    s._plan_name = None
    s._voice_used_s = 0.0
    s._voice_pending_s = 0.0
    s._voice_capped = False
    s._sent = []

    async def _send_error(code, message, fatal=False):  # noqa: ANN001
        s._sent.append((code, message))

    s._send_error = _send_error
    return s


@pytest.fixture
def cap_of():
    """Set the voice cap the way an operator would, and put it back after."""
    settings = get_settings()
    original = (settings.default_plan, settings.quota_enforcement_enabled)

    def _set(seconds: int, *, enforcing: bool = True) -> None:
        settings.plan_limits["test-plan"] = {"voice_seconds": seconds}
        object.__setattr__(settings, "default_plan", "test-plan")
        object.__setattr__(settings, "quota_enforcement_enabled", enforcing)

    yield _set

    object.__setattr__(settings, "default_plan", original[0])
    object.__setattr__(settings, "quota_enforcement_enabled", original[1])
    settings.plan_limits.pop("test-plan", None)


async def _usage(key: str) -> int:
    async with get_sessionmaker()() as db:
        row = await repo.get_daily_usage(db, user_key=key, day=_TODAY)
        return row.voice_seconds if row else 0


async def test_speech_is_counted_and_written(cap_of) -> None:
    cap_of(600)
    s = _session()

    assert await s._meter_voice(_MIC_BYTES_PER_SECOND * 3) is True
    assert s._voice_pending_s == 3.0
    assert await _usage("u7") == 0, "must not write per frame"

    await s._flush_voice_usage()
    assert await _usage("u7") == 3
    assert s._voice_pending_s == 0.0
    assert s._voice_used_s == 3.0


async def test_the_db_is_written_in_batches_not_per_frame(cap_of) -> None:
    # A round-trip per frame would be one every 20-100 ms per live session.
    cap_of(600)
    s = _session()

    for _ in range(14):
        await s._meter_voice(_MIC_BYTES_PER_SECOND)
    assert await _usage("u7") == 0, "flushed too early"

    for _ in range(2):  # crosses _VOICE_FLUSH_EVERY_S = 15
        await s._meter_voice(_MIC_BYTES_PER_SECOND)
    assert await _usage("u7") == 15
    assert s._voice_pending_s == pytest.approx(1.0)


async def test_going_over_the_cap_stops_the_audio_and_says_why(cap_of) -> None:
    cap_of(5)
    s = _session()

    assert await s._meter_voice(_MIC_BYTES_PER_SECOND * 4) is True
    assert await s._meter_voice(_MIC_BYTES_PER_SECOND * 2) is False, "6s > 5s cap"

    code, message = s._sent[-1]
    assert code == "quota_exceeded"
    assert "voice" in message.lower()

    # Every later frame is refused too, and the user is told only once.
    assert await s._meter_voice(_MIC_BYTES_PER_SECOND) is False
    assert len(s._sent) == 1

    # What they used up to the cap is still billed.
    assert await _usage("u7") == 6


async def test_usage_already_on_the_clock_counts(cap_of) -> None:
    # The cap is a daily budget, not a per-session one: someone who already
    # spoke for 4s today gets 1s more, not a fresh 5.
    cap_of(5)
    async with get_sessionmaker()() as db:
        await repo.bump_daily_usage(db, user_key="u7", day=_TODAY, voice_seconds=4)
        await db.commit()

    s = _session()
    await s._load_voice_usage()
    assert s._voice_used_s == 4.0
    assert await s._meter_voice(_MIC_BYTES_PER_SECOND * 2) is False


async def test_yesterday_does_not_count_against_today(cap_of) -> None:
    # Counting an old row would lock someone out permanently after one big day.
    cap_of(5)
    async with get_sessionmaker()() as db:
        await repo.bump_daily_usage(
            db, user_key="u7", day="2020-01-01", voice_seconds=9_999
        )
        await db.commit()

    s = _session()
    await s._load_voice_usage()
    assert s._voice_used_s == 0.0
    assert await s._meter_voice(_MIC_BYTES_PER_SECOND * 2) is True


async def test_nothing_blocks_when_enforcement_is_off(cap_of) -> None:
    # The default posture. Quotas only make sense with real per-user auth, so an
    # operator who hasn't turned them on must never have a call cut off.
    cap_of(1, enforcing=False)
    s = _session()

    assert await s._meter_voice(_MIC_BYTES_PER_SECOND * 99) is True
    assert s._sent == []


async def test_a_failed_write_never_cuts_the_call(cap_of, monkeypatch) -> None:
    # Metering protects the operator's bill. Dropping someone mid-sentence over
    # a database hiccup costs more than the seconds it saves — and the seconds
    # stay pending, so the next flush still bills them.
    cap_of(600)
    s = _session()

    async def boom(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("db is having a moment")

    monkeypatch.setattr(repo, "bump_daily_usage", boom)
    assert await s._meter_voice(_MIC_BYTES_PER_SECOND * 20) is True
    assert s._voice_pending_s == 20.0, "unbilled seconds must not be dropped"

    monkeypatch.undo()
    await s._flush_voice_usage()
    assert await _usage("u7") == 20


async def test_the_session_meters_the_same_person_the_tools_do() -> None:
    # Two spellings of the key would bill one user into two rows, and neither
    # would see the whole picture.
    from app.tools.base import ToolContext
    from app.tools.quota import _user_key

    s = _session(user_id=42)
    ctx = ToolContext(session=None, user_id=42, session_id="test-session")
    assert s._usage_key() == _user_key(ctx) == "u42"
