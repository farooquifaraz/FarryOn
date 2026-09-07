"""A second reading of the user's words: correct on screen, never in the way.

The Live transcriber showed ``ऑल ब्यूटीफुल वाइल्ड`` for audio that a text model
read as ``Call beautiful wife.`` (2026-09-07). The second reading exists to
put the better words on screen; these tests hold the two things that matter
about it: it replaces rather than duplicates, and it can never slow or break
a turn.
"""

from __future__ import annotations

import asyncio
import io
import wave

import pytest

from app.ai.transcript_refiner import pcm_to_wav, refine_transcript

# 16 000 samples x 2 bytes = exactly one second of 16 kHz PCM16 mono.
ONE_SEC = b"\x00\x01" * 16_000


class _Usage:
    prompt_token_count = 123
    candidates_token_count = 7
    total_token_count = 130


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage_metadata = _Usage()


class _Client:
    """Stands in for genai.Client; records what it was asked."""

    def __init__(self, text: str, delay: float = 0.0) -> None:
        self._text = text
        self._delay = delay
        self.calls: list[dict] = []
        self.models = self

    def generate_content(self, *, model, contents):
        self.calls.append({"model": model, "contents": contents})
        if self._delay:
            import time

            time.sleep(self._delay)
        return _Response(self._text)


async def _refine(pcm: bytes, client, **kw):
    return await refine_transcript(
        pcm,
        api_key=kw.pop("api_key", "k"),
        model="m",
        timeout_s=kw.pop("timeout_s", 5),
        client_factory=lambda: client,
    )


@pytest.mark.asyncio
async def test_the_audio_is_sent_as_a_wav_the_model_can_read() -> None:
    client = _Client("Call beautiful wife.")
    assert await _refine(ONE_SEC, client) == "Call beautiful wife."
    part = client.calls[0]["contents"][0]
    assert part.inline_data.mime_type == "audio/wav"
    with wave.open(io.BytesIO(part.inline_data.data), "rb") as w:
        assert w.getframerate() == 16_000
        assert w.getnchannels() == 1
        assert w.getnframes() == 16_000


@pytest.mark.asyncio
async def test_a_cough_is_not_sent_anywhere() -> None:
    """Under 300 ms there are no words; no call, no cost."""
    client = _Client("hm")
    assert await _refine(ONE_SEC[: len(ONE_SEC) // 5], client) is None  # 0.2 s
    assert client.calls == []


@pytest.mark.asyncio
async def test_only_the_last_thirty_seconds_are_read() -> None:
    """A runaway buffer must not become a runaway bill."""
    client = _Client("ok")
    await _refine(ONE_SEC * 45, client)
    part = client.calls[0]["contents"][0]
    with wave.open(io.BytesIO(part.inline_data.data), "rb") as w:
        assert w.getnframes() == 30 * 16_000


@pytest.mark.asyncio
async def test_a_slow_model_is_given_up_on_not_waited_for() -> None:
    client = _Client("late", delay=0.5)
    assert await _refine(ONE_SEC, client, timeout_s=0.05) is None


@pytest.mark.asyncio
async def test_a_failing_model_never_raises() -> None:
    def boom():
        raise RuntimeError("quota")

    text = await refine_transcript(
        ONE_SEC, api_key="k", model="m", timeout_s=5, client_factory=boom
    )
    assert text is None


@pytest.mark.asyncio
async def test_a_second_line_from_the_model_is_not_trusted() -> None:
    client = _Client("Teach me some French.\nNote: the speaker sounds tired.")
    assert await _refine(ONE_SEC, client) == "Teach me some French."


@pytest.mark.asyncio
async def test_no_key_means_no_call() -> None:
    client = _Client("x")
    assert await _refine(ONE_SEC, client, api_key="") is None
    assert client.calls == []


@pytest.mark.asyncio
async def test_each_reading_logs_what_it_cost(monkeypatch) -> None:
    """The bill for this feature is measured from these lines, not guessed."""
    from app.ai import transcript_refiner as tr

    seen: list[dict] = []
    real_info = tr.logger.info

    def spy(event, **kw):
        if event == "transcript.refine_usage":
            seen.append(kw)
        return real_info(event, **kw)

    monkeypatch.setattr(tr.logger, "info", spy)
    await _refine(ONE_SEC, _Client("ok"))
    assert seen and seen[0]["input_tokens"] == 123
    assert seen[0]["output_tokens"] == 7 and seen[0]["audio_s"] == 1.0


def test_wav_wrapper_is_lossless() -> None:
    pcm = bytes(range(256)) * 100
    with wave.open(io.BytesIO(pcm_to_wav(pcm)), "rb") as w:
        assert w.readframes(w.getnframes()) == pcm


# --- the session side: replace, don't duplicate ------------------------------


class _WireGateway:
    """The gateway is irrelevant here; the session only needs it to exist."""


def _session(settings, monkeypatch, sent: list[dict]):
    from app.agent.tool_engine import ToolEngine
    from app.ws.session import Session

    s = Session(
        object(),
        gateway_factory=lambda _p, _s: _WireGateway(),
        engine=ToolEngine.from_tools([]),
        settings=settings,
    )
    s._mode = "agent"

    async def capture(payload):
        sent.append(payload)

    async def quiet(*_a, **_k):
        return None

    monkeypatch.setattr(s, "_send_json", capture)
    monkeypatch.setattr(s, "_send_state", quiet)
    monkeypatch.setattr(s, "_send_audio_frame", quiet)
    return s


def _fakes(monkeypatch):
    """A fake store that hands out row ids and records updates, plus a fake
    second reader. Returns (saved rows, updates) for the test to inspect."""
    from app.ws import session as session_mod

    saved: list[list] = []      # [role, text] per row, index = row id
    updates: list[tuple[int, str]] = []

    async def fake_save(session_id, role, text):
        saved.append([role, text])
        return len(saved) - 1

    async def fake_update(row_id, text):
        updates.append((row_id, text))
        saved[row_id][1] = text

    monkeypatch.setattr(session_mod, "repo_safe_transcript", fake_save)
    monkeypatch.setattr(session_mod, "repo_safe_update_transcript", fake_update)
    return saved, updates


@pytest.mark.asyncio
async def test_refined_first_then_live_final_gives_one_correct_row(monkeypatch) -> None:
    """Long reply: the second reading lands before the reply ends."""
    from app.ai.events import AudioStartEvent, TranscriptEvent
    from app.config import get_settings
    from app.ws import session as session_mod

    settings = get_settings().model_copy(deep=True)
    settings.refine_user_transcripts = True
    settings.gemini_api_key = "k"
    sent: list[dict] = []
    saved, updates = _fakes(monkeypatch)

    async def fake_refine(pcm, **kw):
        return "Call beautiful wife."

    monkeypatch.setattr(session_mod, "refine_transcript", fake_refine)
    s = _session(settings, monkeypatch, sent)

    s._turn_pcm += ONE_SEC
    await s._handle_event(AudioStartEvent())
    await s._refine_task
    refined = [m for m in sent if m.get("refined")]
    assert len(refined) == 1 and refined[0]["text"] == "Call beautiful wife."
    assert refined[0]["role"] == "user" and refined[0]["final"] is True
    assert saved == []  # nothing stored yet; the Live final will carry it

    await s._handle_event(
        TranscriptEvent(role="user", text="ऑल ब्यूटीफुल वाइल्ड", final=True)
    )
    assert saved == [["user", "Call beautiful wife."]]
    assert updates == []


@pytest.mark.asyncio
async def test_live_final_first_then_refined_corrects_the_row(monkeypatch) -> None:
    """Short reply: the reply ends (and the Live final saves) before the
    3-second second reading lands. Device-seen 2026-09-07 as TWO rows."""
    from app.ai.events import AudioStartEvent, TranscriptEvent
    from app.config import get_settings
    from app.ws import session as session_mod

    settings = get_settings().model_copy(deep=True)
    settings.refine_user_transcripts = True
    settings.gemini_api_key = "k"
    sent: list[dict] = []
    saved, updates = _fakes(monkeypatch)

    gate = asyncio.Event()

    async def slow_refine(pcm, **kw):
        await gate.wait()
        return "Teach me some French"

    monkeypatch.setattr(session_mod, "refine_transcript", slow_refine)
    s = _session(settings, monkeypatch, sent)

    s._turn_pcm += ONE_SEC
    await s._handle_event(AudioStartEvent())
    # The reply ends first: Live final saved as row 0.
    await s._handle_event(
        TranscriptEvent(role="user", text="Teach me some friends.", final=True)
    )
    assert saved == [["user", "Teach me some friends."]]
    # Now the second reading lands.
    gate.set()
    await s._refine_task
    assert updates == [(0, "Teach me some French")]
    assert saved == [["user", "Teach me some French"]]  # corrected, not added


@pytest.mark.asyncio
async def test_a_turn_correction_never_touches_the_next_turn(monkeypatch) -> None:
    """The stale one-shot flag ate the FOLLOWING turn's text on the device."""
    from app.ai.events import AudioStartEvent, TranscriptEvent, TurnCompleteEvent
    from app.config import get_settings
    from app.ws import session as session_mod

    settings = get_settings().model_copy(deep=True)
    settings.refine_user_transcripts = True
    settings.gemini_api_key = "k"
    sent: list[dict] = []
    saved, updates = _fakes(monkeypatch)

    gate = asyncio.Event()

    async def slow_refine(pcm, **kw):
        await gate.wait()
        return "Teach me some French"

    monkeypatch.setattr(session_mod, "refine_transcript", slow_refine)
    s = _session(settings, monkeypatch, sent)

    # Turn 0: Live final first, reply completes, turn index advances.
    s._turn_pcm += ONE_SEC
    await s._handle_event(AudioStartEvent())
    await s._handle_event(
        TranscriptEvent(role="user", text="Teach me some friends.", final=True)
    )
    await s._handle_event(TurnCompleteEvent())
    # Turn 1's Live final arrives while turn 0's reading is still pending.
    await s._handle_event(
        TranscriptEvent(role="user", text="What is your favorite movie", final=True)
    )
    gate.set()
    await s._refine_task
    assert saved == [
        ["user", "Teach me some French"],          # turn 0, corrected in place
        ["user", "What is your favorite movie"],   # turn 1, untouched
    ]


@pytest.mark.asyncio
async def test_switch_off_means_no_reading_at_all(monkeypatch) -> None:
    from app.ai.events import AudioStartEvent
    from app.config import get_settings
    from app.ws import session as session_mod

    settings = get_settings().model_copy(deep=True)
    settings.refine_user_transcripts = False
    called: list[int] = []

    async def fake_refine(pcm, **kw):
        called.append(1)
        return "x"

    monkeypatch.setattr(session_mod, "refine_transcript", fake_refine)
    s = _session(settings, monkeypatch, [])
    s._turn_pcm += ONE_SEC
    await s._handle_event(AudioStartEvent())
    assert s._refine_task is None and called == []


@pytest.mark.asyncio
async def test_a_failed_reading_leaves_the_live_text_to_be_saved(monkeypatch) -> None:
    """When the second reader returns nothing, the first reading stands and
    is saved exactly as before the feature existed."""
    from app.ai.events import AudioStartEvent, TranscriptEvent
    from app.config import get_settings
    from app.ws import session as session_mod

    settings = get_settings().model_copy(deep=True)
    settings.refine_user_transcripts = True
    settings.gemini_api_key = "k"
    sent: list[dict] = []

    async def fake_refine(pcm, **kw):
        return None

    monkeypatch.setattr(session_mod, "refine_transcript", fake_refine)
    saved, updates = _fakes(monkeypatch)
    s = _session(settings, monkeypatch, sent)
    s._turn_pcm += ONE_SEC
    await s._handle_event(AudioStartEvent())
    await s._refine_task
    await s._handle_event(TranscriptEvent(role="user", text="ऑल ब्यूटीफुल", final=True))
    assert not any(m.get("refined") for m in sent)
    assert saved == [["user", "ऑल ब्यूटीफुल"]] and updates == []
