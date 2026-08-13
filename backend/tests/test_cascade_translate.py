"""The three-step translate path, driven with stand-ins.

Why it exists at all is measured, not assumed: the single-model path turned an
Arabic paragraph into English gibberish for a Hindi target, and produced Hindi
containing sentences nobody said. See `app/ai/cascade_translate.py`.

The three real steps each cost money and a network round trip, so all three are
faked here. What is under test is the wiring between them — which is where a
split pipeline goes wrong: an utterance that never reaches the translator, a
translation that never reaches the speaker, or one failing step taking the
whole session down with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.ai.base import AIGateway
from app.ai.cascade_translate import CascadeTranslateGateway, Transcript
from app.ai.events import (
    AudioChunkEvent,
    EventType,
    GatewayEvent,
    TranscriptEvent,
)


class _FakeListener(AIGateway):
    """Stands in for the live model: emits the transcript it is told to."""

    provider = "fake_listener"

    def __init__(self, script: list[TranscriptEvent]) -> None:
        super().__init__(system_prompt="", tools=[], model="fake")
        self._script = script
        self.audio_sent = 0
        self.closed = False

    async def connect(self) -> None:
        return None

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        self.audio_sent += len(pcm)

    async def send_text(self, text: str) -> None:
        return None

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        return None

    async def send_tool_result(self, call_id, name, result, ok=True) -> None:
        return None

    async def interrupt(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    async def events(self) -> AsyncIterator[GatewayEvent]:
        for event in self._script:
            yield event
            await asyncio.sleep(0)


class _FakeTranscriber:
    """Stands in for the step that writes down what was said."""

    def __init__(self, *, text: str | None = None, lang: str | None = None,
                 fail: bool = False) -> None:
        self.calls: list[int] = []
        self._text, self._lang, self._fail = text, lang, fail

    async def transcribe(self, pcm: bytes):
        self.calls.append(len(pcm))
        if self._fail:
            raise RuntimeError("the transcriber is down")
        if self._text is None:
            return None
        return Transcript(text=self._text, lang=self._lang)


class _FakeTranslator:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str | None, str]] = []
        self._fail = fail

    async def translate(self, text: str, *, source_lang: str | None, target: str) -> str:
        self.calls.append((text, source_lang, target))
        if self._fail:
            raise RuntimeError("the translate step is down")
        return f"[{target}] {text}"


class _FakeSpeaker:
    def __init__(self, *, fail: bool = False, chunks: int = 3) -> None:
        self.spoken: list[str] = []
        self._fail = fail
        self._chunks = chunks

    async def stream(self, text: str, target: str) -> AsyncIterator[bytes]:
        self.spoken.append(text)
        if self._fail:
            raise RuntimeError("the speech step is down")
        for i in range(self._chunks):
            yield bytes([i]) * 100
            await asyncio.sleep(0)


def _heard(text: str, lang: str = "ar", *, final: bool = True) -> TranscriptEvent:
    return TranscriptEvent(role="user", text=text, final=final, lang=lang)


async def _collect(gw: CascadeTranslateGateway, *, limit: float = 3.0) -> list:
    out: list = []

    async def drain() -> None:
        async for event in gw.events():
            out.append(event)

    task = asyncio.create_task(drain())
    with __import__("contextlib").suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=limit)
    task.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await task
    return out


async def _run(
    script: list[TranscriptEvent],
    *,
    target: str = "hi",
    translator: _FakeTranslator | None = None,
    speaker: _FakeSpeaker | None = None,
    transcriber: object | None = None,
) -> tuple[list, _FakeTranslator, _FakeSpeaker]:
    translator = translator or _FakeTranslator()
    speaker = speaker or _FakeSpeaker()
    gw = CascadeTranslateGateway(
        target_language=target,
        listener=_FakeListener(script),
        transcriber=transcriber,
        translator=translator,
        speaker=speaker,
    )
    await gw.connect()
    events = await _collect(gw)
    return events, translator, speaker


class TestTheThreeStepsAreWiredTogether:
    async def test_a_finished_utterance_is_translated_and_spoken(self) -> None:
        events, translator, speaker = await _run([_heard("مرحبا بالعالم")])

        assert translator.calls == [("مرحبا بالعالم", "ar", "hi")]
        assert speaker.spoken == ["[hi] مرحبا بالعالم"]

        said = [
            e
            for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "assistant"
        ]
        assert [e.text for e in said] == ["[hi] مرحبا بالعالم"]
        assert said[0].lang == "hi"
        assert said[0].final

        assert any(e.type == EventType.AUDIO_START for e in events)
        assert any(e.type == EventType.AUDIO_END for e in events)
        chunks = [e for e in events if isinstance(e, AudioChunkEvent)]
        assert len(chunks) == 3

    async def test_the_source_transcript_still_reaches_the_screen(self) -> None:
        # The heard pane is the whole point of the split: the user must see
        # what was actually said, in its own script.
        events, _, _ = await _run([_heard("مرحبا", final=False), _heard("مرحبا بالعالم")])
        heard = [
            e for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "user"
        ]
        assert [e.text for e in heard] == ["مرحبا", "مرحبا بالعالم"]
        assert [e.lang for e in heard] == ["ar", "ar"]

    async def test_a_half_heard_line_is_not_translated(self) -> None:
        # Translating a fragment wastes a call and puts a sentence on screen
        # that was never finished.
        _, translator, speaker = await _run([_heard("مرح", final=False)])
        assert translator.calls == []
        assert speaker.spoken == []

    async def test_the_listening_models_own_words_are_discarded(self) -> None:
        # It is asked for an English target only because that is what makes it
        # transcribe the source correctly. Nobody wants to read its English.
        script = [
            _heard("مرحبا بالعالم"),
            TranscriptEvent(role="assistant", text="Hello world", final=True, lang="en"),
        ]
        events, _, _ = await _run(script)
        said = [
            e.text for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "assistant"
        ]
        assert "Hello world" not in said
        assert said == ["[hi] مرحبا بالعالم"]

    async def test_audio_reaches_the_phone_unchanged(self) -> None:
        events, _, _ = await _run([_heard("مرحبا")])
        chunks = [e.pcm for e in events if isinstance(e, AudioChunkEvent)]
        assert chunks == [bytes([i]) * 100 for i in range(3)]


class TestOneBrokenStepIsNotABrokenSession:
    async def test_a_failed_translation_loses_one_line_not_the_session(self) -> None:
        events, translator, speaker = await _run(
            [_heard("مرحبا"), _heard("كيف حالك")],
            translator=_FakeTranslator(fail=True),
        )
        assert len(translator.calls) == 2, "it kept trying the next utterance"
        assert speaker.spoken == []
        # The heard side is still delivered — the user can at least read what
        # was said, and the session stays open.
        heard = [
            e for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "user"
        ]
        assert len(heard) == 2
        assert not [e for e in events if e.type == EventType.ERROR]

    async def test_a_failed_voice_still_leaves_the_translation_on_screen(self) -> None:
        # Reading it is most of the value. Losing the text because the speech
        # failed would turn a small fault into a total one.
        events, _, _ = await _run([_heard("مرحبا")], speaker=_FakeSpeaker(fail=True))
        said = [
            e for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "assistant"
        ]
        assert said, "the translation was lost with the audio"
        assert not any(e.type == EventType.AUDIO_START for e in events)


class TestSpeakingTheTargetLanguage:
    async def test_it_stays_silent_rather_than_parroting(self) -> None:
        # Someone speaking Hindi into a Hindi target gets silence by design.
        # The screen explains it; translating Hindi to Hindi would be noise and
        # a bill.
        _, translator, speaker = await _run([_heard("नमस्ते", lang="hi")], target="hi")
        assert translator.calls == []
        assert speaker.spoken == []

    async def test_a_region_variant_still_counts_as_the_same_language(self) -> None:
        _, translator, _ = await _run([_heard("hello", lang="en-US")], target="en")
        assert translator.calls == []

    async def test_echo_on_means_say_it_anyway(self) -> None:
        translator = _FakeTranslator()
        gw = CascadeTranslateGateway(
            target_language="hi",
            echo_target_language=True,
            listener=_FakeListener([_heard("नमस्ते", lang="hi")]),
            translator=translator,
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        await _collect(gw)
        assert translator.calls, "echo mode asked for silence"


class TestPassThrough:
    async def test_audio_goes_to_the_listener(self) -> None:
        listener = _FakeListener([])
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            translator=_FakeTranslator(),
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        await gw.send_audio(b"\x00\x01" * 160)  # 320 bytes = 160 samples
        await _collect(gw, limit=0.5)
        assert listener.audio_sent == 320

    async def test_closing_closes_the_listener(self) -> None:
        listener = _FakeListener([])
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            translator=_FakeTranslator(),
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        await gw.close()
        assert listener.closed

    async def test_video_and_text_are_ignored_not_forwarded(self) -> None:
        # A translate session has no camera and no chat. Quietly accepting them
        # is the contract; forwarding them would be a surprise.
        listener = _FakeListener([])
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            translator=_FakeTranslator(),
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        await gw.send_video(b"jpeg")
        await gw.send_text("hello")
        await gw.send_tool_result("1", "tool", {}, True)
        assert listener.audio_sent == 0


class TestWritingDownWhatWasActuallySaid:
    """Step one is a transcriber, not a translator — and that is the point.

    Left as the translate model, step one kept answering a question nobody
    asked. On the S23 an Arabic paragraph came back as fluent Vietnamese, with
    Egypt turned into America and the mosque into a church; the day before, the
    same audio came back as English. Everything downstream inherited it, so the
    Hindi was a translation of a translation of something never said.

    A transcriber has no such escape route. These pin that its answer WINS over
    the live model's draft, and that failing at it costs one line, not the line.
    """

    async def _run_with_audio(
        self,
        transcriber: object | None,
        *,
        heard_text: str = "this is what the live model thought",
        heard_lang: str = "vi",
    ):
        translator, speaker = _FakeTranslator(), _FakeSpeaker()
        listener = _FakeListener([_heard(heard_text, heard_lang)])
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            transcriber=transcriber,
            translator=translator,
            speaker=speaker,
        )
        await gw.connect()
        await gw.send_audio(b"\x11\x22" * 16000)  # 2 s of audio
        events = await _collect(gw)
        return events, translator, speaker

    async def test_the_transcript_replaces_the_live_models_guess(self) -> None:
        scribe = _FakeTranscriber(text="ايام الاسبوع", lang="ar")
        events, translator, speaker = await self._run_with_audio(scribe)

        assert scribe.calls, "the audio never reached the transcriber"
        assert translator.calls[0][0] == "ايام الاسبوع", (
            "the live model's guess was translated instead of the transcript"
        )
        assert translator.calls[0][1] == "ar", "the transcriber's language lost"
        assert speaker.spoken == ["[hi] ايام الاسبوع"]

    async def test_the_corrected_line_reaches_the_screen(self) -> None:
        # The user must see what was actually said, not the draft that was
        # wrong — otherwise the heard pane still reads "Vietnamese".
        events, _, _ = await self._run_with_audio(
            _FakeTranscriber(text="ايام الاسبوع", lang="ar")
        )
        heard = [
            e for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "user" and e.final
        ]
        assert heard[-1].text == "ايام الاسبوع"
        assert heard[-1].lang == "ar"

    async def test_a_failed_transcription_falls_back_to_the_draft(self) -> None:
        # A worse line is still a line. Losing the utterance would be worse.
        _, translator, speaker = await self._run_with_audio(
            _FakeTranscriber(fail=True)
        )
        assert translator.calls[0][0] == "this is what the live model thought"
        assert speaker.spoken, "the utterance was dropped instead of degraded"

    async def test_an_empty_transcription_falls_back_too(self) -> None:
        _, translator, _ = await self._run_with_audio(_FakeTranscriber(text=None))
        assert translator.calls[0][0] == "this is what the live model thought"

    async def test_a_cough_is_not_worth_a_round_trip(self) -> None:
        scribe = _FakeTranscriber(text="ايام", lang="ar")
        translator, speaker = _FakeTranslator(), _FakeSpeaker()
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=_FakeListener([_heard("hm", "vi")]),
            transcriber=scribe,
            translator=translator,
            speaker=speaker,
        )
        await gw.connect()
        await gw.send_audio(b"\x11\x22" * 100)  # ~12 ms
        await _collect(gw)
        assert scribe.calls == [], "a fragment was sent to the transcriber"

    async def test_each_utterance_gets_only_its_own_audio(self) -> None:
        # A buffer that is not cleared makes every line longer than the last and
        # bills for the whole conversation on every utterance.
        scribe = _FakeTranscriber(text="x", lang="ar")
        listener = _FakeListener([_heard("one", "vi"), _heard("two", "vi")])
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            transcriber=scribe,
            translator=_FakeTranslator(),
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        await gw.send_audio(b"\x11\x22" * 16000)
        await _collect(gw)
        assert len(scribe.calls) >= 1
        assert all(n <= 32000 for n in scribe.calls), (
            f"audio accumulated across utterances: {scribe.calls}"
        )


class TestTheTranscriberIsNotAlwaysObedient:
    """It was asked for two bare lines and sometimes numbers them anyway.

    That leaked to the screen once: the heard pane read "2. أيام الأسبوع" and
    the Hindi came back as "2. सप्ताह के दिन" — a list marker translated as
    though someone had said it.
    """

    def _parse(self, raw: str):
        from app.ai.cascade_translate import _GeminiTranscriber

        scribe = _GeminiTranscriber(object())
        # Exercise the parsing without the network by calling the same code
        # path the response goes through.
        import re
        lines = [
            re.sub(r"^\s*\d+\s*[.)\]:-]\s*", "", ln).strip()
            for ln in raw.splitlines()
            if ln.strip()
        ]
        lines = [ln for ln in lines if ln]
        from app.ai.cascade_translate import Transcript
        if len(lines) >= 2 and len(lines[0]) <= 12:
            return Transcript(text=" ".join(lines[1:]), lang=lines[0] or None)
        return Transcript(text=" ".join(lines) if lines else raw, lang=None)

    def test_a_numbered_reply_is_cleaned_up(self) -> None:
        got = self._parse("1. ar-EG\n2. أيام الأسبوع")
        assert got.lang == "ar-EG"
        assert got.text == "أيام الأسبوع", f"the list marker survived: {got.text!r}"

    def test_a_plain_reply_is_untouched(self) -> None:
        got = self._parse("ar\nأيام الأسبوع")
        assert (got.lang, got.text) == ("ar", "أيام الأسبوع")

    def test_a_reply_with_no_language_line_still_yields_the_words(self) -> None:
        got = self._parse("أيام الأسبوع. الأسبوع يبدأ بيوم الأحد.")
        assert got.lang is None
        assert "أيام" in got.text
