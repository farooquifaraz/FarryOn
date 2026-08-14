"""The three-step translate path, driven with stand-ins.

Why it exists at all is measured, not assumed: the single-model path turned an
Arabic paragraph into English gibberish for a Hindi target, and produced Hindi
containing sentences nobody said. Step one is now a streaming recogniser — see
`app/ai/gemini_asr.py` and its own suite for what that fixed.

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
from app.ai.cascade_translate import CascadeTranslateGateway
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



class _FakeTranslator:
    def __init__(self, *, fail: bool = False, detected: str | None = None) -> None:
        self.calls: list[tuple[str, str | None, str]] = []
        #: What each call was given as the preceding utterance. Kept apart from
        #: `calls` so the older assertions keep reading as they did.
        self.contexts: list[str | None] = []
        self._fail = fail
        self.detected = detected

    async def translate(
        self,
        text: str,
        *,
        source_lang: str | None,
        target: str,
        previous: str | None = None,
    ) -> tuple[str, str | None]:
        """Returns the translation AND the language it was translated from.

        The second value is why the heard pane has a name above it again: the
        recogniser reports no language, so the translator — which has just read
        the sentence — says what it was.
        """
        self.calls.append((text, source_lang, target))
        self.contexts.append(previous)
        if self._fail:
            raise RuntimeError("the translate step is down")
        return f"[{target}] {text}", self.detected


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
) -> tuple[list, _FakeTranslator, _FakeSpeaker]:
    translator = translator or _FakeTranslator()
    speaker = speaker or _FakeSpeaker()
    gw = CascadeTranslateGateway(
        target_language=target,
        listener=_FakeListener(script),
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





class TestASentenceCutInTwoStillReadsAsOne:
    """Sentences do not always break where the recogniser says they do.

    A speaker listing things pauses at the commas, and the model writes a full
    stop into the pause. Spanish "…para trabajar. Estudiar o comunicarse con
    amigos." is one sentence cut in two that way, and the second half was
    translated as an instruction to go and study (device-seen 2026-08-14).

    Nothing here can put the sentence back together — the cut has already
    happened upstream. What it can do is stop the second half being read in
    isolation, and that does not depend on the cut being right.
    """

    async def test_each_utterance_is_told_what_came_before_it(self) -> None:
        listener = _FakeListener([
            TranscriptEvent(role="user", final=True, lang="es", utterance=0,
                            text="Cada día, más de 4.900 millones de personas "
                                 "se conectan a internet para trabajar."),
            TranscriptEvent(role="user", final=True, lang="es", utterance=1,
                            text="Estudiar o comunicarse con amigos."),
        ])
        translator = _FakeTranslator()
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            translator=translator,
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        await _collect(gw)

        assert len(translator.contexts) == 2
        assert translator.contexts[0] is None, "there was nothing before the first"
        assert translator.contexts[1] is not None
        assert "para trabajar" in translator.contexts[1], (
            "the second half was translated with no idea what it continues"
        )

    async def test_the_context_is_never_the_sentence_itself(self) -> None:
        # A sentence handed its own text as context would invite the model to
        # translate it twice over.
        listener = _FakeListener([
            TranscriptEvent(role="user", text="Uno.", final=True, lang="es",
                            utterance=0),
            TranscriptEvent(role="user", text="Dos.", final=True, lang="es",
                            utterance=1),
        ])
        translator = _FakeTranslator()
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            translator=translator,
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        await _collect(gw)

        for (text, _, _), context in zip(translator.calls, translator.contexts):
            assert context != text


class TestEverySentenceKeepsItsName:
    """Translation runs while the speaker keeps talking, so answers come back
    out of order and after the next sentence is already on screen. Every event
    therefore carries the number of the sentence it belongs to.

    Without it the client attached each translation to whatever was newest: on
    the device the first sentence of a paragraph lost its Hindi entirely and
    the second briefly wore it (2026-08-14).
    """

    async def test_the_translation_carries_its_sentences_number(self) -> None:
        listener = _FakeListener([
            TranscriptEvent(role="user", text="مرحبا", final=True, lang="ar",
                            utterance=7),
        ])
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            translator=_FakeTranslator(),
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        events = await _collect(gw)

        said = [
            e for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "assistant"
        ]
        assert said, "nothing was translated"
        assert said[0].utterance == 7, (
            "the translation cannot be matched to its sentence"
        )

    async def test_the_heard_line_gets_its_language_back(self) -> None:
        # The recogniser reports no language, so the heard pane read a bare
        # "HEARD". The translator has just read the sentence and knows.
        listener = _FakeListener([
            TranscriptEvent(role="user", text="مرحبا", final=True, lang=None,
                            utterance=0),
        ])
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            translator=_FakeTranslator(detected="ar"),
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        events = await _collect(gw)

        named = [
            e for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "user" and e.lang
        ]
        assert named, "the heard line never got a language"
        assert named[-1].lang == "ar"
        assert named[-1].utterance == 0, "it would land on the wrong card"
        assert named[-1].text == "مرحبا", "the words must not change"

    async def test_a_language_already_known_is_not_re_sent(self) -> None:
        # Re-sending an unchanged line is a wasted message and a chance to
        # flicker the screen for nothing.
        listener = _FakeListener([
            TranscriptEvent(role="user", text="مرحبا", final=True, lang="ar",
                            utterance=0),
        ])
        gw = CascadeTranslateGateway(
            target_language="hi",
            listener=listener,
            translator=_FakeTranslator(detected="ar"),
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        events = await _collect(gw)

        heard = [
            e for e in events
            if e.type == EventType.TRANSCRIPT and e.role == "user"
        ]
        assert len(heard) == 1, f"the heard line was sent twice: {heard}"


class TestFragmentsNobodySaid:
    """A fragment carrying no letters or digits is not speech.

    A live session produced a card whose whole content was ``.`` and paid
    to translate it into ``.`` (device-seen 2026-08-14).
    """

    async def test_a_lone_full_stop_is_never_translated(self) -> None:
        listener = _FakeListener([
            TranscriptEvent(role="user", text=".", final=True, lang="en",
                            utterance=0),
            TranscriptEvent(role="user", text="Bilkul theek hai.", final=True,
                            lang="hi", utterance=1),
        ])
        translator = _FakeTranslator()
        gw = CascadeTranslateGateway(
            target_language="en",
            listener=listener,
            translator=translator,
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        events = await _collect(gw)

        assert len(translator.calls) == 1, (
            "a full stop was sent to the translator and billed for"
        )
        heard = [e.text for e in events
                 if isinstance(e, TranscriptEvent) and e.role == "user"]
        assert "." not in heard, (
            'a card reading "." reads as a bug to whoever is looking at it'
        )

    async def test_a_number_said_alone_still_counts(self) -> None:
        # Digits are speech. "49億" and "4.900 millones" are the whole point
        # of translating numbers carefully; a rule that wanted letters would
        # throw them away.
        listener = _FakeListener([
            TranscriptEvent(role="user", text="49", final=True, lang="ja",
                            utterance=0),
        ])
        translator = _FakeTranslator()
        gw = CascadeTranslateGateway(
            target_language="en",
            listener=listener,
            translator=translator,
            speaker=_FakeSpeaker(),
        )
        await gw.connect()
        await _collect(gw)

        assert len(translator.calls) == 1, "a number said on its own is a turn"
