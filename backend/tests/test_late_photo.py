"""The glasses photo that arrives after its question gave up waiting.

Pinned here: a vision tool on the glasses waits only the photo patience and
then says the photo is on its way; the question is answered exactly once when
the photo lands, or told honestly that it never came; a phone camera and a
final failure behave exactly as before; and a photo that had already arrived
is no longer cut off by the engine's 20 s default.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.late_photo import LatePhoto
from app.agent.orchestrator import Orchestrator
from app.agent.tool_engine import ToolEngine
from app.tools import quota
from app.tools.base import Tool, ToolContext
from app.tools.capture_feedback import PHOTO_ON_ITS_WAY
from app.tools.device import CapturePhotoTool
from app.tools.identify import IdentifyImageTool

pytestmark = pytest.mark.asyncio


class _Calls:
    def __init__(self) -> None:
        self.answers: list[tuple[bytes, str | None]] = []
        self.failures: list[str | None] = []

    async def answer(self, jpeg: bytes, question: str | None) -> None:
        self.answers.append((jpeg, question))

    async def fail(self, reason: str | None) -> None:
        self.failures.append(reason)


def _tracker(calls: _Calls, window: float = 30.0, clock=None) -> LatePhoto:
    kw = {"clock": clock} if clock else {}
    return LatePhoto(
        window_seconds=window, answer=calls.answer, fail=calls.fail, **kw
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# -- the tracker ------------------------------------------------------------


async def test_a_late_photo_answers_the_question_once() -> None:
    calls = _Calls()
    late = _tracker(calls)
    late.defer("what does it say?")
    assert late.pending
    assert late.on_frame(b"jpeg") is True
    assert late.on_frame(b"second") is False, "answered once, not per photo"
    await _settle()
    assert calls.answers == [(b"jpeg", "what does it say?")]
    assert calls.failures == []
    await late.close()


async def test_no_question_waiting_leaves_a_photo_alone() -> None:
    calls = _Calls()
    late = _tracker(calls)
    assert late.on_frame(b"jpeg") is False
    await _settle()
    assert calls.answers == [] and calls.failures == []


async def test_a_capture_timeout_keeps_waiting_for_the_photo() -> None:
    # Device 2026-09-23: the app reported capture_timeout, then the glasses
    # took the photo anyway. It must still answer the question.
    calls = _Calls()
    late = _tracker(calls)
    late.defer(None)
    late.on_failure("capture_timeout")
    assert late.pending
    late.on_frame(b"jpeg")
    await _settle()
    assert calls.answers == [(b"jpeg", None)]
    assert calls.failures == []
    await late.close()


async def test_a_final_failure_is_said_once_and_ends_the_question() -> None:
    calls = _Calls()
    late = _tracker(calls)
    late.defer(None)
    late.on_failure("not_connected")
    assert not late.pending
    late.on_frame(b"jpeg")
    await _settle()
    assert calls.failures == ["not_connected"]
    assert calls.answers == []


async def test_a_photo_that_never_comes_is_admitted_at_the_window() -> None:
    calls = _Calls()
    late = _tracker(calls, window=0.05)
    late.defer(None)
    await asyncio.sleep(0.12)
    assert not late.pending
    assert calls.failures == ["capture_timeout"]
    await late.close()


async def test_a_photo_after_the_window_is_not_an_answer() -> None:
    now = [0.0]
    calls = _Calls()
    late = _tracker(calls, window=10.0, clock=lambda: now[0])
    late.defer(None)
    now[0] = 11.0  # the expiry task has not run yet; the clock says too late
    assert late.on_frame(b"jpeg") is False
    await _settle()
    assert calls.answers == []
    await late.close()


async def test_a_newer_question_replaces_the_older_one() -> None:
    calls = _Calls()
    late = _tracker(calls, window=0.05)
    late.defer("first")
    late.defer("second")
    late.on_frame(b"jpeg")
    await asyncio.sleep(0.12)  # past the first question's window
    assert calls.answers == [(b"jpeg", "second")]
    assert calls.failures == [], "the replaced question's expiry was cancelled"
    await late.close()


async def test_cancel_and_close_say_nothing() -> None:
    calls = _Calls()
    late = _tracker(calls, window=0.05)
    late.defer(None)
    late.cancel()
    await asyncio.sleep(0.12)
    late.defer(None)
    await late.close()
    await asyncio.sleep(0.12)
    assert calls.answers == [] and calls.failures == []


async def test_a_handler_error_never_escapes() -> None:
    async def boom(jpeg: bytes, question: str | None) -> None:
        raise RuntimeError("vision down")

    async def fail(reason: str | None) -> None:
        return None

    late = LatePhoto(window_seconds=30.0, answer=boom, fail=fail)
    late.defer(None)
    late.on_frame(b"jpeg")
    await _settle()
    await late.close()


# -- the orchestrator routes photos and failures to it ----------------------


def _orchestrator() -> Orchestrator:
    async def notify(_: dict) -> None:
        return None

    return Orchestrator(
        engine=ToolEngine(),
        gateway=SimpleNamespace(),
        sessionmaker=None,
        notify_client=notify,
    )


async def test_a_photo_with_no_tool_waiting_goes_to_the_late_question() -> None:
    orch = _orchestrator()
    calls = _Calls()
    orch.late_photo = _tracker(calls)
    orch.late_photo.defer("q")
    orch.last_frame = b"jpeg"
    orch.notify_new_frame()
    await _settle()
    assert calls.answers == [(b"jpeg", "q")]
    await orch.late_photo.close()


async def test_a_photo_a_tool_is_waiting_for_is_not_answered_twice() -> None:
    orch = _orchestrator()
    calls = _Calls()
    orch.late_photo = _tracker(calls)
    orch.late_photo.defer("older question")
    waiter = asyncio.create_task(orch.wait_for_frame(timeout=5.0))
    await _settle()
    orch.last_frame = b"jpeg"
    orch.notify_new_frame()
    assert await waiter is True
    await _settle()
    assert calls.answers == [], "the waiting tool answers; the late path stays quiet"
    assert not orch.late_photo.pending
    await orch.late_photo.close()


async def test_a_failure_with_no_tool_waiting_goes_to_the_late_question() -> None:
    orch = _orchestrator()
    calls = _Calls()
    orch.late_photo = _tracker(calls)
    orch.late_photo.defer(None)
    orch.notify_capture_failed("transfer_stalled")
    await _settle()
    assert calls.failures == ["transfer_stalled"]


# -- the vision tools --------------------------------------------------------


def _quota_off(monkeypatch) -> None:
    monkeypatch.setattr(
        quota, "get_settings", lambda: SimpleNamespace(quota_enforcement_enabled=False)
    )


class _Wait:
    """wait_for_frame double: records the timeout it was given."""

    def __init__(self) -> None:
        self.timeouts: list[float | None] = []

    async def __call__(self, timeout: float | None = None) -> bool:
        self.timeouts.append(timeout)
        return False


def _ctx(db, *, patience, error=None, deferred=None):
    wait = _Wait()
    return wait, ToolContext(
        session=db,
        session_id="late",
        wait_for_frame=wait,
        capture_error=lambda: error,
        latest_frame=lambda: (None, None),
        photo_patience=patience,
        defer_photo=(deferred.append if deferred is not None else None),
    )


@pytest.mark.parametrize("tool", [IdentifyImageTool(), CapturePhotoTool()])
async def test_on_the_glasses_a_slow_photo_is_on_its_way(db_session, monkeypatch, tool) -> None:
    _quota_off(monkeypatch)
    deferred: list[str | None] = []
    wait, ctx = _ctx(db_session, patience=12.0, deferred=deferred)
    kwargs = {"question": "what does it say?"} if tool.name == "identify_image" else {}
    out = await tool.run(ctx, **kwargs)
    assert wait.timeouts == [12.0], "waited only the patience"
    assert out["pending"] is True and out["_instruction"] == PHOTO_ON_ITS_WAY
    assert deferred == ([kwargs["question"]] if kwargs else [None])


@pytest.mark.parametrize("tool", [IdentifyImageTool(), CapturePhotoTool()])
async def test_a_capture_timeout_is_deferred_too(db_session, monkeypatch, tool) -> None:
    _quota_off(monkeypatch)
    deferred: list[str | None] = []
    _, ctx = _ctx(db_session, patience=12.0, error="capture_timeout", deferred=deferred)
    out = await tool.run(ctx)
    assert out.get("pending") is True
    assert len(deferred) == 1


@pytest.mark.parametrize("tool", [IdentifyImageTool(), CapturePhotoTool()])
async def test_a_final_failure_is_reported_as_before(db_session, monkeypatch, tool) -> None:
    _quota_off(monkeypatch)
    deferred: list[str | None] = []
    _, ctx = _ctx(db_session, patience=12.0, error="not_connected", deferred=deferred)
    out = await tool.run(ctx)
    assert "pending" not in out
    assert "Bluetooth" in (out.get("error") or out.get("_instruction") or "")
    assert deferred == []


@pytest.mark.parametrize("tool", [IdentifyImageTool(), CapturePhotoTool()])
async def test_a_phone_camera_is_unchanged(db_session, monkeypatch, tool) -> None:
    _quota_off(monkeypatch)
    deferred: list[str | None] = []
    wait, ctx = _ctx(db_session, patience=None, deferred=deferred)
    out = await tool.run(ctx)
    assert wait.timeouts == [None], "the session default, as before"
    assert "pending" not in out
    assert deferred == []


# -- the engine honours a tool's own ceiling ---------------------------------


class _Slow(Tool):
    name = "slow"
    description = "sleeps"
    parameters = {"type": "object", "properties": {}, "required": []}
    timeout_seconds = 0.3

    async def run(self, ctx: ToolContext, **kwargs):
        await asyncio.sleep(0.1)
        return {"ok": True}


async def test_a_tool_ceiling_beats_the_engine_default() -> None:
    engine = ToolEngine.from_tools([_Slow()], timeout_seconds=0.05)
    result = await engine.dispatch("slow", {}, ToolContext(session=None))
    assert result.ok is True


async def test_vision_tools_outlast_patience_plus_describe() -> None:
    from app.config import Settings

    s = Settings()
    for tool in (IdentifyImageTool(), CapturePhotoTool()):
        assert tool.timeout_seconds is not None
        assert tool.timeout_seconds > s.glasses_photo_patience_seconds + 15.0
    # The app gives up (38 s) inside the late window, so a photo it still
    # delivers is never dropped for arriving after the window.
    assert s.glasses_late_photo_seconds > 38.0


# -- the session speaks the late answer --------------------------------------


class _Gateway:
    def __init__(self) -> None:
        self.video: list[bytes] = []
        self.images: list[bytes] = []
        self.texts: list[str] = []

    async def send_video(self, jpeg: bytes, ts_ms=None) -> None:
        self.video.append(jpeg)

    async def attach_image(self, jpeg: bytes) -> None:
        self.images.append(jpeg)

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    def set_camera_kind(self, kind) -> None:
        self.kind = kind


def _session(gateway: _Gateway):
    from app.config import get_settings
    from app.ws.session import Session

    session = Session(
        object(),
        gateway_factory=lambda _p, _s: gateway,
        engine=ToolEngine.from_tools([]),
        settings=get_settings(),
    )
    session._gateway = gateway
    return session


async def test_the_late_photo_is_shown_described_and_answered(monkeypatch) -> None:
    import app.services.vision as vision

    async def fake_detect(mode, *, settings, image_data, question):
        assert question == "what does the sign say?"
        return {"ok": True, "result": {"answer": "A sign reading EXIT."}}

    monkeypatch.setattr(vision, "run_detection", fake_detect)
    gateway = _Gateway()
    session = _session(gateway)
    await session._answer_late_photo(b"jpeg", "what does the sign say?")
    assert gateway.video == [b"jpeg"] and gateway.images == [b"jpeg"], (
        "in the model's context, so a follow-up question is about this photo"
    )
    assert len(gateway.texts) == 1
    assert "EXIT" in gateway.texts[0]


async def test_a_failed_describe_still_answers_from_the_photo(monkeypatch) -> None:
    import app.services.vision as vision

    async def broken(*a, **k):
        raise RuntimeError("vision api down")

    monkeypatch.setattr(vision, "run_detection", broken)
    gateway = _Gateway()
    session = _session(gateway)
    await session._answer_late_photo(b"jpeg", None)
    assert len(gateway.texts) == 1 and "attached" in gateway.texts[0]


async def test_the_late_answer_waits_for_the_model_to_finish_speaking() -> None:
    gateway = _Gateway()
    session = _session(gateway)
    session._last_state = "speaking"
    task = asyncio.create_task(session._say_when_quiet("note"))
    await asyncio.sleep(0.3)
    assert gateway.texts == [], "never spoken over the model's own sentence"
    session._last_state = "listening"
    await asyncio.wait_for(task, timeout=2.0)
    assert gateway.texts == ["note"]


async def test_the_failure_is_said_in_plain_words() -> None:
    gateway = _Gateway()
    session = _session(gateway)
    await session._fail_late_photo("transfer_stalled")
    assert len(gateway.texts) == 1
    assert "never arrived" in gateway.texts[0]
    assert "transfer" in gateway.texts[0]


async def test_the_patience_follows_the_camera() -> None:
    gateway = _Gateway()
    session = _session(gateway)
    session._orchestrator = _orchestrator()
    await session._dispatch_control({"type": "device_update", "videoKind": "phone+glasses"})
    assert session._orchestrator.photo_patience == session._settings.glasses_photo_patience_seconds
    await session._dispatch_control({"type": "device_update", "videoKind": "phone"})
    assert session._orchestrator.photo_patience is None


async def test_the_late_photo_reaches_the_model_once() -> None:
    # Device 2026-09-23: the frame gate forwarded the late photo AND the late
    # answer forwarded it again — the same image twice in the context.
    from app.config import Settings
    from app.ws.frames import FrameTag, encode_frame
    from app.ws.session import Session

    gateway = _Gateway()
    session = Session(
        object(),
        gateway_factory=lambda *a: None,
        engine=ToolEngine.from_tools([]),
        settings=Settings(vision_frame_mode="on_turn", vision_frame_heartbeat_s=0.0),
    )
    session._gateway = gateway
    session._orchestrator = _orchestrator()
    answered: list[bytes] = []

    async def answer(jpeg: bytes, question: str | None) -> None:
        answered.append(jpeg)

    async def fail(reason: str | None) -> None:
        return None

    session._orchestrator.late_photo = LatePhoto(
        window_seconds=30.0, answer=answer, fail=fail
    )
    session._orchestrator.late_photo.defer(None)
    await session._handle_binary(encode_frame(FrameTag.INPUT_VIDEO, b"jpeg", 0))
    await _settle()
    assert gateway.video == [], "the gate leaves it to the late answer"
    assert answered == [b"jpeg"]
    # With nothing deferred the gate behaves exactly as before.
    await session._handle_binary(encode_frame(FrameTag.INPUT_VIDEO, b"next", 0))
    assert gateway.video == [b"next"]
    await session._orchestrator.late_photo.close()
