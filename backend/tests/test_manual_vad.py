"""Manual activity detection for a glasses microphone (PROTOCOL.md §3).

The glasses' call-mode audio made the model's automatic detector fire on the
room (empty turns) or miss the user's onset for 20-40 s (live 2026-09-13).
With a glasses mic the session now runs the detector MANUALLY: the app's
energy gate sends ``speech_start`` / ``speech_end`` and those become the
model's activityStart / activityEnd. The phone mic keeps the automatic
detector, byte for byte.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.ws.session import Session


class _Gateway:
    provider = "fake"

    def __init__(self) -> None:
        self.manual_vad = False
        self.calls: list[str] = []

    async def send_activity_start(self) -> None:
        self.calls.append("start")

    async def send_activity_end(self) -> None:
        self.calls.append("end")


class _NoManualGateway:
    """An adapter without the knob (mock, OpenAI): stays automatic."""

    provider = "plain"

    async def send_activity_start(self) -> None:  # pragma: no cover - never
        raise AssertionError("must not be called")


def _session(**settings) -> Session:
    return Session(
        object(),
        gateway_factory=lambda *args: None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        settings=Settings(refine_user_transcripts=False, **settings),
    )


@pytest.mark.parametrize(
    ("kind", "manual"),
    [
        ("glasses", True),
        ("glasses+phone", True),  # glasses mic, phone camera
        ("phone", False),
        ("phone+glasses", False),  # phone mic, glasses camera
        (None, False),
    ],
)
def test_manual_mode_follows_the_microphone_in_hello(kind, manual) -> None:
    s = _session()
    hello = {"device": {"kind": kind}} if kind is not None else {}
    assert s._wants_manual_vad(hello) is manual


def test_the_setting_switches_it_off_entirely() -> None:
    s = _session(manual_vad_for_glasses=False)
    assert s._wants_manual_vad({"device": {"kind": "glasses"}}) is False


def test_apply_sets_the_gateway_knob_when_it_has_one() -> None:
    s = _session()
    s._manual_vad = True
    gw = _Gateway()
    s._gateway = gw  # type: ignore[assignment]
    s._apply_vad_mode()
    assert gw.manual_vad is True
    assert s._manual_vad is True


def test_apply_falls_back_to_automatic_without_the_knob() -> None:
    s = _session()
    s._manual_vad = True
    s._gateway = _NoManualGateway()  # type: ignore[assignment]
    s._apply_vad_mode()
    assert s._manual_vad is False


@pytest.mark.asyncio
async def test_speech_markers_bracket_the_model_turn_in_manual_mode() -> None:
    s = _session()
    gw = _Gateway()
    s._gateway = gw  # type: ignore[assignment]
    s._manual_vad = True
    await s._dispatch_control({"type": "speech_start"})
    await s._dispatch_control({"type": "speech_end"})
    assert gw.calls == ["start", "end"]


@pytest.mark.asyncio
async def test_speech_markers_are_informational_under_automatic_detection() -> None:
    s = _session()
    gw = _Gateway()
    s._gateway = gw  # type: ignore[assignment]
    s._manual_vad = False
    await s._dispatch_control({"type": "speech_start"})
    await s._dispatch_control({"type": "speech_end"})
    assert gw.calls == []


@pytest.mark.asyncio
async def test_a_mic_switch_that_changes_the_mode_reconnects() -> None:
    s = _session()
    s._mode = "agent"
    s._gateway = _Gateway()  # type: ignore[assignment]
    s._manual_vad = False
    closed: list[bool] = []

    async def fake_close() -> None:
        closed.append(True)

    s._close_for_reconnect = fake_close  # type: ignore[method-assign]
    await s._dispatch_control({"type": "device_update", "videoKind": "phone", "audioKind": "phone"})
    assert closed == [], "same mode: nothing to do"
    await s._dispatch_control({"type": "device_update", "videoKind": "phone", "audioKind": "glasses"})
    assert closed == [True]


def test_gemini_config_disables_automatic_detection_in_manual_mode() -> None:
    types = pytest.importorskip("google.genai").types
    from app.ai.gemini import GeminiGateway

    gw = GeminiGateway(system_prompt="x", tools=[])
    gw.manual_vad = True
    cfg = gw._build_config()
    aad = cfg.realtime_input_config.automatic_activity_detection
    assert aad.disabled is True

    gw.manual_vad = False
    cfg = gw._build_config()
    aad = cfg.realtime_input_config.automatic_activity_detection
    assert not aad.disabled
    assert isinstance(aad, types.AutomaticActivityDetection)
