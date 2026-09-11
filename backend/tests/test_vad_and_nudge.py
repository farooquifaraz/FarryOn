"""Explicit voice-activity settings, and the stuck-turn nudge.

Outdoors on 2026-09-09 the provider's detector went 28 s and then 67 s with
mic audio flowing and no turn opened, then delivered everything as one 33 s
turn. Two answers: the detector's knobs made explicit (off by default, so a
blank configuration is the request it always was), and a session-side nudge
that closes the audio stream when audio has flowed unheard for too long.
"""

from __future__ import annotations

import pytest

from app.agent.tool_engine import ToolEngine
from app.ai.gemini import GeminiGateway
from app.config import Settings
from app.ws.session import Session

pytestmark = pytest.mark.asyncio


class _Settings:
    def __init__(self, **kw):
        self.gemini_vad_start_sensitivity = kw.get("start", "")
        self.gemini_vad_end_sensitivity = kw.get("end", "")
        self.gemini_vad_silence_ms = kw.get("silence", 0)
        self.gemini_vad_prefix_padding_ms = kw.get("padding", 0)


def _types():
    from google.genai import types  # type: ignore[import-not-found]

    return types


# ---- #1: the detector's knobs --------------------------------------------------

def test_blank_settings_leave_vad_at_the_provider_default() -> None:
    cfg = GeminiGateway._activity_detection(_types(), _Settings())
    assert cfg.disabled is False
    assert cfg.start_of_speech_sensitivity is None
    assert cfg.end_of_speech_sensitivity is None
    assert cfg.silence_duration_ms is None
    assert cfg.prefix_padding_ms is None


def test_every_knob_is_passed_when_set() -> None:
    types = _types()
    cfg = GeminiGateway._activity_detection(
        types, _Settings(start="high", end="HIGH", silence=700, padding=300)
    )
    assert cfg.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_HIGH
    assert cfg.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_HIGH
    assert cfg.silence_duration_ms == 700
    assert cfg.prefix_padding_ms == 300


def test_a_wrong_word_is_ignored_not_fatal() -> None:
    cfg = GeminiGateway._activity_detection(_types(), _Settings(start="medium", end="yes"))
    assert cfg.start_of_speech_sensitivity is None
    assert cfg.end_of_speech_sensitivity is None


def test_the_defaults_are_the_device_tested_configuration() -> None:
    # 2026-09-11: start "high" alone (17/17 heard first time), END knobs at
    # the provider default ("high" + 600 ms stopped turns closing at all),
    # the quiet nudge at 2 s, the flowing-audio cap at 20 s.
    f = Settings.model_fields
    assert f["gemini_vad_start_sensitivity"].default == "high"
    assert f["gemini_vad_end_sensitivity"].default == ""
    assert f["gemini_vad_silence_ms"].default == 0
    assert f["gemini_vad_prefix_padding_ms"].default == 0
    assert f["stuck_turn_nudge_seconds"].default == 20.0


# ---- #2: the nudge --------------------------------------------------------------

class _Gateway:
    def __init__(self):
        self.ends = 0

    async def send_audio_stream_end(self):
        self.ends += 1


class _NudgeSettings:
    stuck_turn_nudge_seconds = 20.0


def _session(gateway=None, settings=None) -> Session:
    s = Session(
        object(),
        gateway_factory=lambda _p, _s: object(),
        engine=ToolEngine.from_tools([]),
        settings=settings or _NudgeSettings(),
    )
    s._gateway = gateway or _Gateway()
    return s


async def test_unheard_audio_for_the_limit_closes_the_stream_once() -> None:
    gw = _Gateway()
    s = _session(gw)
    await s._note_unheard_audio(100.0)
    await s._note_unheard_audio(110.0)
    await s._note_unheard_audio(119.0)
    assert gw.ends == 0, "under the limit: nothing"
    await s._note_unheard_audio(120.5)
    assert gw.ends == 1
    # The window restarts from the nudge, not from the first frame.
    await s._note_unheard_audio(130.0)
    assert gw.ends == 1
    await s._note_unheard_audio(141.0)
    assert gw.ends == 2


async def test_being_heard_or_replying_resets_the_window() -> None:
    gw = _Gateway()
    s = _session(gw)
    await s._note_unheard_audio(100.0)
    s._t_user_first = 105.0  # the provider transcribed the user: heard
    await s._note_unheard_audio(125.0)
    assert gw.ends == 0
    assert s._unheard_audio_since == 0.0
    s._t_user_first = 0.0
    s._t_reply_started = 126.0  # the assistant is speaking
    await s._note_unheard_audio(150.0)
    assert gw.ends == 0


async def test_a_turn_ending_resets_the_window() -> None:
    gw = _Gateway()
    s = _session(gw)
    await s._note_unheard_audio(100.0)
    s._t_user_last = 101.0
    s._log_turn_timing(outcome="complete")
    assert s._unheard_audio_since == 0.0
    await s._note_unheard_audio(119.0)  # a fresh window from here
    assert gw.ends == 0


async def test_off_by_default_and_never_fatal() -> None:
    class Off:
        stuck_turn_nudge_seconds = 0.0

    gw = _Gateway()
    s = _session(gw, Off())
    for t in (100.0, 200.0, 300.0):
        await s._note_unheard_audio(t)
    assert gw.ends == 0

    class Boom:
        async def send_audio_stream_end(self):
            raise RuntimeError("socket gone")

    s = _session(Boom())
    await s._note_unheard_audio(100.0)
    await s._note_unheard_audio(121.0)  # raises inside, swallowed


# ---- #2b: the quiet-triggered nudge ---------------------------------------------

class _QuietSettings:
    stuck_turn_nudge_seconds = 20.0
    stuck_turn_quiet_nudge_seconds = 2.0


async def test_a_pause_after_unheard_speech_nudges_at_once() -> None:
    gw = _Gateway()
    s = _session(gw, _QuietSettings())
    s._mode = "agent"
    # Audio flowed 100.0-101.5 and nobody was heard.
    await s._note_unheard_audio(100.0)
    s._last_audio_frame_at = 101.5
    assert await s._maybe_quiet_nudge(102.0) is False, "the pause is too short"
    assert await s._maybe_quiet_nudge(103.6) is True
    assert gw.ends == 1
    # Once per pause.
    assert await s._maybe_quiet_nudge(105.0) is False
    assert gw.ends == 1
    # More audio, another pause: another nudge.
    s._last_audio_frame_at = 110.0
    await s._note_unheard_audio(110.0)
    assert await s._maybe_quiet_nudge(112.5) is True
    assert gw.ends == 2


async def test_no_quiet_nudge_while_heard_or_replying_or_before_any_audio() -> None:
    gw = _Gateway()
    s = _session(gw, _QuietSettings())
    s._mode = "agent"
    assert await s._maybe_quiet_nudge(50.0) is False
    await s._note_unheard_audio(100.0)
    s._last_audio_frame_at = 100.0
    s._t_user_first = 100.5  # heard
    assert await s._maybe_quiet_nudge(105.0) is False
    s._t_user_first = 0.0
    s._t_reply_started = 101.0  # replying
    assert await s._maybe_quiet_nudge(105.0) is False
    assert gw.ends == 0


def test_the_quiet_nudge_is_on_by_default() -> None:
    assert Settings.model_fields["stuck_turn_quiet_nudge_seconds"].default == 2.0


# ---- #2c: a deaf connection is dropped so the app reconnects ---------------------

class _ReconnectSettings:
    stuck_turn_nudge_seconds = 20.0
    stuck_turn_quiet_nudge_seconds = 2.0
    stuck_reconnect_after_nudges = 3


async def _speak_then_pause(s, start: float) -> None:
    """Audio from `start`, gate quiet 2.5 s later, nothing heard."""
    await s._note_unheard_audio(start)
    s._last_audio_frame_at = start + 1.0
    await s._maybe_quiet_nudge(start + 3.5)


async def test_three_unheard_utterances_drop_the_socket() -> None:
    gw = _Gateway()
    s = _session(gw, _ReconnectSettings())
    s._mode = "agent"
    closed = []

    async def fake_close():
        closed.append(True)

    s._close_for_reconnect = fake_close  # type: ignore[method-assign]
    await _speak_then_pause(s, 100.0)
    await _speak_then_pause(s, 110.0)
    assert gw.ends == 2 and closed == []
    await _speak_then_pause(s, 120.0)  # third: give up, reconnect
    assert closed == [True]
    assert gw.ends == 2, "no nudge on the attempt that drops the socket"


async def test_being_heard_resets_the_count() -> None:
    gw = _Gateway()
    s = _session(gw, _ReconnectSettings())
    s._mode = "agent"
    closed = []

    async def fake_close():
        closed.append(True)

    s._close_for_reconnect = fake_close  # type: ignore[method-assign]
    await _speak_then_pause(s, 100.0)
    await _speak_then_pause(s, 110.0)
    s._t_user_first = 115.0  # heard at last
    await s._note_unheard_audio(116.0)  # resets everything
    s._t_user_first = 0.0
    s._log_turn_timing(outcome="complete")
    await _speak_then_pause(s, 200.0)
    await _speak_then_pause(s, 210.0)
    assert closed == [], "two fresh nudges after a heard turn are not three"


async def test_flowing_audio_alone_never_reconnects() -> None:
    """Music keeps the gate open: cap nudges, never a dropped socket."""
    gw = _Gateway()
    s = _session(gw, _ReconnectSettings())
    s._mode = "agent"
    closed = []

    async def fake_close():
        closed.append(True)

    s._close_for_reconnect = fake_close  # type: ignore[method-assign]
    t = 100.0
    for _ in range(6):
        await s._note_unheard_audio(t)
        t += 21.0
    assert gw.ends >= 5
    assert closed == []
