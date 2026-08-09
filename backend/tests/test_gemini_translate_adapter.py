"""What the real Gemini translate model actually does, pinned as tests.

Every rule here was measured against the live model on 2026-08-10, not guessed
from documentation. The mock cannot teach us any of it, and each one was a bug
in the adapter before the probe found it:

* The model **never sends ``turn_complete``** — 57 messages, not one boundary.
  Utterances are closed by a quiet gap instead. Without that nothing is ever
  final: the client renders every line as provisional forever and the buffers
  grow for the length of the session.
* Transcript text arrives as **deltas** (``'Good morning, everyone.'`` then
  ``' The meeting'`` then ``' will start at'``), leading spaces included.
* Both sides report a ``language_code`` — ``en`` in, ``hi`` out.
* The output stream is **padded with pure digital silence**, indefinitely:
  15.2 s of zeros after 3.5 s of speech, which is ~170 MB/hour of nothing over
  a phone's mobile data.

Driven against the adapter's message handlers with stand-in objects, so the
rules stay covered without a key or a network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.ai.events import EventType
from app.ai.gemini_translate import (
    _SILENCE_GRACE_CHUNKS,
    _UTTERANCE_GAP_S,
    GeminiTranslateGateway,
)


def _msg(
    *,
    heard: str | None = None,
    heard_lang: str | None = None,
    translated: str | None = None,
    translated_lang: str | None = None,
    audio: bytes | None = None,
) -> SimpleNamespace:
    """One server message shaped the way the real one is."""
    content = SimpleNamespace(
        input_transcription=(
            SimpleNamespace(text=heard, language_code=heard_lang)
            if heard is not None
            else None
        ),
        output_transcription=(
            SimpleNamespace(text=translated, language_code=translated_lang)
            if translated is not None
            else None
        ),
        model_turn=(
            SimpleNamespace(
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=audio))]
            )
            if audio is not None
            else None
        ),
        interrupted=False,
        turn_complete=False,
    )
    return SimpleNamespace(server_content=content)


def _gw() -> GeminiTranslateGateway:
    return GeminiTranslateGateway(target_language="hi")


async def _drain(gw: GeminiTranslateGateway) -> list:
    out = []
    while not gw._queue.empty():
        out.append(gw._queue.get_nowait())
    return out


class TestTranscripts:
    async def test_deltas_accumulate_into_one_utterance(self) -> None:
        gw = _gw()
        for part in ("Good morning, everyone.", " The meeting", " will start"):
            await gw._handle_message(_msg(heard=part, heard_lang="en"))

        events = [e for e in await _drain(gw) if e.type == EventType.TRANSCRIPT]
        assert events[-1].text == "Good morning, everyone. The meeting will start"
        assert all(not e.final for e in events), "nothing finalises on its own"

    async def test_the_detected_language_is_carried_not_assumed(self) -> None:
        gw = _gw()
        await gw._handle_message(_msg(heard="Bonjour", heard_lang="fr"))
        heard = [
            e
            for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.role == "user"
        ]
        assert heard[-1].lang == "fr"

    async def test_the_translation_reports_its_own_language(self) -> None:
        # Reported, not taken from config: a target the model silently
        # substituted should show up as what it actually is.
        gw = _gw()
        await gw._handle_message(_msg(translated="सुप्रभात", translated_lang="hi"))
        out = [
            e
            for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.role == "assistant"
        ]
        assert out[-1].lang == "hi"


class TestUtteranceBoundaries:
    async def test_a_quiet_gap_finalises_both_sides(self) -> None:
        gw = _gw()
        await gw._handle_message(_msg(heard="Hello there.", heard_lang="en"))
        await gw._handle_message(_msg(translated="नमस्ते।", translated_lang="hi"))
        await _drain(gw)

        # Pretend the gap has elapsed rather than sleeping through it.
        gw._last_delta_at -= _UTTERANCE_GAP_S + 0.1
        await gw._finalize(turn_complete=True)

        finals = [
            e
            for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final
        ]
        assert {e.role for e in finals} == {"user", "assistant"}
        assert gw._heard_buf == "" and gw._translated_buf == ""

    async def test_the_watchdog_closes_an_utterance_by_itself(self) -> None:
        # The behaviour the whole feature depends on, since the model provides
        # no boundary of its own.
        gw = _gw()
        gw._watchdog_task = asyncio.create_task(gw._utterance_watchdog())
        try:
            await gw._handle_message(_msg(heard="One two three.", heard_lang="en"))
            await _drain(gw)
            await asyncio.sleep(_UTTERANCE_GAP_S + 0.6)
            finals = [
                e
                for e in await _drain(gw)
                if e.type == EventType.TRANSCRIPT and e.final
            ]
            assert finals, "the utterance never closed"
            assert finals[0].text == "One two three."
        finally:
            gw._closed = True
            gw._watchdog_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await gw._watchdog_task

    async def test_an_endless_monologue_is_broken_up(self) -> None:
        # Someone who never pauses must not produce one line that grows for an
        # hour — the UI would stop moving and the string would never be freed.
        gw = _gw()
        await gw._handle_message(_msg(heard="x" * 500, heard_lang="en"))
        finals = [
            e
            for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final
        ]
        assert finals, "a long monologue was never broken"
        assert gw._heard_buf == ""


class TestSilencePadding:
    async def test_a_short_silence_is_passed_through(self) -> None:
        # A pause between sentences is part of how speech sounds.
        gw = _gw()
        await gw._handle_audio(b"\x11\x22" * 100)  # real audio
        for _ in range(_SILENCE_GRACE_CHUNKS):
            await gw._handle_audio(b"\x00" * 200)

        chunks = [
            e for e in await _drain(gw) if e.type == EventType.AUDIO_CHUNK
        ]
        assert len(chunks) == 1 + _SILENCE_GRACE_CHUNKS

    async def test_the_endless_padding_is_dropped(self) -> None:
        # 15.2 s of zeros followed 3.5 s of speech, and it keeps coming for as
        # long as the session is open. Forwarding it is ~170 MB/hour of nothing.
        gw = _gw()
        await gw._handle_audio(b"\x11\x22" * 100)
        for _ in range(_SILENCE_GRACE_CHUNKS + 60):
            await gw._handle_audio(b"\x00" * 200)

        events = await _drain(gw)
        chunks = [e for e in events if e.type == EventType.AUDIO_CHUNK]
        assert len(chunks) == 1 + _SILENCE_GRACE_CHUNKS, "padding was forwarded"
        assert any(e.type == EventType.AUDIO_END for e in events), (
            "the audio segment must be closed when the padding starts"
        )

    async def test_real_audio_after_padding_reopens_the_segment(self) -> None:
        gw = _gw()
        for _ in range(_SILENCE_GRACE_CHUNKS + 10):
            await gw._handle_audio(b"\x00" * 200)
        await _drain(gw)

        await gw._handle_audio(b"\x33\x44" * 100)
        events = await _drain(gw)
        assert any(e.type == EventType.AUDIO_START for e in events)
        assert any(e.type == EventType.AUDIO_CHUNK for e in events)

    async def test_quiet_but_real_audio_is_not_mistaken_for_padding(self) -> None:
        # Only EXACTLY zero samples are padding. A faint passage is real audio
        # and a translator that dropped it would be dropping speech.
        gw = _gw()
        faint = b"\x01\x00" * 100
        for _ in range(_SILENCE_GRACE_CHUNKS + 10):
            await gw._handle_audio(faint)
        chunks = [
            e for e in await _drain(gw) if e.type == EventType.AUDIO_CHUNK
        ]
        assert len(chunks) == _SILENCE_GRACE_CHUNKS + 10


class TestTheContract:
    async def test_it_accepts_no_tools_and_no_system_prompt(self) -> None:
        # The model takes neither. An adapter that stored them would invite
        # someone to start passing them.
        gw = GeminiTranslateGateway(
            target_language="hi",
            system_prompt="you are helpful",
            tools=[object()],  # type: ignore[list-item]
        )
        assert gw.system_prompt == ""
        assert gw.tools == []

    async def test_video_and_text_are_accepted_and_ignored(self) -> None:
        # Accepted rather than raising: the session must be able to hand this
        # gateway anything a confused client sends without dying.
        gw = _gw()
        await gw.send_video(b"\xff\xd8")
        await gw.send_text("hello")
        await gw.send_tool_result("1", "create_note", {"ok": True})
        assert gw._queue.empty()

class TestConcurrency:
    async def test_two_finalisers_cannot_emit_the_same_utterance_twice(
        self,
    ) -> None:
        """The receive loop and the watchdog both close utterances.

        They interleave at every ``await``. If the buffers were cleared after
        the queue writes rather than before, both would see the same text and
        the user would watch their sentence appear twice.
        """
        gw = _gw()
        await gw._handle_message(_msg(heard="Only once.", heard_lang="en"))
        await _drain(gw)

        await asyncio.gather(
            gw._finalize(turn_complete=True),
            gw._finalize(turn_complete=True),
        )

        finals = [
            e
            for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final
        ]
        assert len(finals) == 1, f"emitted {len(finals)} copies of one utterance"
        assert finals[0].text == "Only once."

    async def test_finalising_an_empty_utterance_emits_nothing(self) -> None:
        # The watchdog ticks every 250 ms whether or not anyone is speaking.
        gw = _gw()
        await gw._finalize(turn_complete=True)
        assert await _drain(gw) == []
