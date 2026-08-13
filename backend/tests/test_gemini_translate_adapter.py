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
    _MIN_SENTENCE_CHARS,
    _MAX_REOPENS,
    _SENTENCE_GAP_S,
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

class TestWhenAnUtteranceIsFinished:
    """A pause to think is not the same as a full stop.

    One timeout for both is what chopped a long sentence into
    "register account in all three services and" / "generate" / "or time to
    register" / "account in" — four cards, device-seen 2026-08-10. The model
    does emit punctuation, so it can be asked.
    """

    async def test_a_finished_sentence_closes_quickly(self) -> None:
        gw = _gw()
        await gw._handle_message(_msg(heard="Good morning, everyone.", heard_lang="en"))
        assert gw._gap_needed() == _SENTENCE_GAP_S

    async def test_an_unfinished_one_is_given_time(self) -> None:
        gw = _gw()
        await gw._handle_message(
            _msg(heard="The meeting will start at", heard_lang="en")
        )
        assert gw._gap_needed() == _UTTERANCE_GAP_S
        assert _UTTERANCE_GAP_S > _SENTENCE_GAP_S * 2, (
            "the whole point is that an unfinished sentence waits materially "
            "longer"
        )

    async def test_trailing_space_does_not_hide_the_full_stop(self) -> None:
        # The deltas arrive with leading and trailing spaces.
        gw = _gw()
        await gw._handle_message(_msg(heard="Yes, of course.  ", heard_lang="en"))
        assert gw._gap_needed() == _SENTENCE_GAP_S

    async def test_it_reads_the_scripts_this_is_used_in(self) -> None:
        for text in ("बैठक शुरू होगी।", "هل انت بخير؟", "会議は四時です。"):
            gw = _gw()
            await gw._handle_message(_msg(heard=text, heard_lang="xx"))
            assert gw._gap_needed() == _SENTENCE_GAP_S, text

    async def test_a_comma_is_not_the_end_of_anything(self) -> None:
        gw = _gw()
        await gw._handle_message(_msg(heard="First of all,", heard_lang="en"))
        assert gw._gap_needed() == _UTTERANCE_GAP_S

class _LogRecorder:
    """Stands in for a structlog logger and keeps what it was handed.

    Accepts any level, because the point is to see EVERYTHING the module writes
    while a translation is in flight — a privacy assertion that only inspected
    ``info`` would miss a stray ``warning`` carrying the same words.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def _record(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))

    debug = info = warning = error = exception = critical = _record

    def by_event(self, event: str) -> list[dict]:
        return [fields for name, fields in self.calls if name == event]

    def everything_written(self) -> str:
        """Event names and every field value, as one searchable string."""
        return " ".join(
            [name for name, _ in self.calls]
            + [str(v) for _, fields in self.calls for v in fields.values()]
        )


class TestWhatTheLogsCanAnswer:
    """A language question must be answerable without recording the words.

    Faraz asked why Arabic came back labelled English. The server logs held
    every timing and no language at all, so the only way to answer was to
    reproduce it against the model. A language code is not content — the
    speaker gives nothing away by having it written down — and it is exactly
    the field that settles the question.
    """

    # Both tests stand in for the module's own logger rather than reading
    # stdout. ``configure_logging`` sets ``cache_logger_on_first_use=True``, so
    # once anything in the suite has started the app the module logger is frozen
    # with a JSON renderer and the stream it was built on — neither ``capsys``
    # nor ``structlog.testing.capture_logs`` can reach it after that. These two
    # went green alone and red in the full suite until this was understood.
    # Recording the call is also the more honest assertion: what matters is the
    # fields we hand the logger, not how they are rendered today.

    async def test_the_detected_language_is_logged(self, monkeypatch) -> None:
        rec = _LogRecorder()
        monkeypatch.setattr("app.ai.gemini_translate.logger", rec)
        gw = _gw()
        await gw._handle_message(_msg(heard="Bonjour tout le monde.", heard_lang="fr"))
        await gw._handle_message(
            _msg(translated="सभी को नमस्ते।", translated_lang="hi")
        )
        await gw._finalize(turn_complete=True)

        said = rec.by_event("gemini_translate.utterance")
        assert said, "no utterance record logged"
        assert said[0]["heard_lang"] == "fr", "the detected language was not recorded"
        assert said[0]["target"] == "hi"

    async def test_the_words_themselves_are_never_logged(self, monkeypatch) -> None:
        rec = _LogRecorder()
        monkeypatch.setattr("app.ai.gemini_translate.logger", rec)
        gw = _gw()
        await gw._handle_message(
            _msg(heard="my card number is four one one one", heard_lang="en")
        )
        await gw._handle_message(
            _msg(translated="मेरा कार्ड नंबर चार एक एक एक है", translated_lang="hi")
        )
        await gw._finalize(turn_complete=True)

        assert rec.by_event("gemini_translate.utterance"), "nothing was logged at all"
        # Every field of every record, not just the ones we expect: a translator
        # is pointed at other people's conversations, and they never agreed to
        # anything.
        written = rec.everything_written()
        assert "card number" not in written, "heard content reached the log"
        assert "कार्ड" not in written, "translated content reached the log"


class _ScriptedUpstream:
    """A ``receive()`` that plays one scripted batch per socket.

    The real client re-enters ``receive()`` after every turn, so each call
    returns a fresh generator — the same shape, minus the network.
    """

    def __init__(self, batch: list) -> None:
        self._batch = batch

    def receive(self):
        batch, self._batch = self._batch, []

        async def gen():
            for message in batch:
                yield message

        return gen()


def _goaway() -> SimpleNamespace:
    """What the Live API sends shortly before it hangs up."""
    return SimpleNamespace(go_away=SimpleNamespace(time_left="10s"))


class TestTheSocketDoesNotLastTheConversation:
    """The Live API caps how long one upstream socket may live.

    It sends a GoAway first and expects the client to reconnect. We did not
    know the word, so at about ten minutes the upstream hung up and its raw
    protocol error was handed to the phone as fatal — the user read
    *"1008 None. Connection aborted because the client failed to close the
    connection after receiving a GoAway signal once the session durat"*, cut
    off mid-word (S23, 2026-08-11, 9m43s into a session).

    Ten minutes does not cover a conversation, a meeting, or an appointment.
    """

    def _wire(self, gw, monkeypatch, scripts: list[list]) -> dict:
        """Give `gw` a sequence of scripted sockets; stop when they run out."""
        state = {"opened": 0}

        async def fake_open() -> None:
            if state["opened"] >= len(scripts):
                gw._closed = True  # nothing left to play; let the loop finish
                gw._session = _ScriptedUpstream([])
                return
            gw._session = _ScriptedUpstream(list(scripts[state["opened"]]))
            state["opened"] += 1

        monkeypatch.setattr(gw, "_open_upstream", fake_open)
        return state

    async def test_a_goaway_rolls_the_socket_over_and_carries_on(
        self, monkeypatch
    ) -> None:
        gw = _gw()
        state = self._wire(
            gw,
            monkeypatch,
            [
                [_msg(heard="Buenos días.", heard_lang="es"), _goaway()],
                [_msg(heard="La reunión empieza.", heard_lang="es")],
            ],
        )
        await gw._open_upstream()
        await gw._receive_loop()

        assert state["opened"] >= 2, "the socket was never replaced"
        errors = [e for e in await _drain(gw) if e is not None
                  and e.type == EventType.ERROR]
        assert not errors, "a rollover is not something the user should hear about"

    async def test_the_words_in_flight_survive_the_rollover(
        self, monkeypatch
    ) -> None:
        gw = _gw()
        self._wire(gw, monkeypatch, [[_msg(heard="Buenos días.", heard_lang="es"),
                                      _goaway()]])
        await gw._open_upstream()
        await gw._receive_loop()

        finals = [
            e for e in await _drain(gw)
            if e is not None and e.type == EventType.TRANSCRIPT and e.final
        ]
        assert finals, "the last thing said was dropped when the socket rolled"
        assert finals[-1].text == "Buenos días."

    async def test_the_upstreams_own_words_never_reach_the_user(
        self, monkeypatch
    ) -> None:
        gw = _gw()

        class _Exploding:
            def receive(self):
                async def gen():
                    raise RuntimeError(
                        "1008 None. Connection aborted because the client "
                        "failed to close the connection after receiving a "
                        "GoAway signal once the session durat"
                    )
                    yield  # pragma: no cover - unreachable, keeps it a generator

                return gen()

        async def fake_open() -> None:
            gw._session = _Exploding()

        monkeypatch.setattr(gw, "_open_upstream", fake_open)
        await gw._open_upstream()
        await gw._receive_loop()

        errors = [e for e in await _drain(gw) if e is not None
                  and e.type == EventType.ERROR]
        assert errors, "giving up silently is worse than saying so"
        said = errors[-1].message
        assert "GoAway" not in said and "1008" not in said, (
            f"raw upstream text reached the user: {said!r}"
        )
        assert said.endswith("."), "and it was cut off mid-word"

    async def test_it_stops_reopening_rather_than_billing_forever(
        self, monkeypatch
    ) -> None:
        # A socket that dies the instant it opens is not a long conversation.
        gw = _gw()
        opens = {"n": 0}

        async def fake_open() -> None:
            opens["n"] += 1
            gw._session = _ScriptedUpstream([])

        monkeypatch.setattr(gw, "_open_upstream", fake_open)
        await gw._open_upstream()
        await gw._receive_loop()

        assert opens["n"] <= _MAX_REOPENS + 2, (
            f"reopened {opens['n']} times — that is a billing loop"
        )
        errors = [e for e in await _drain(gw) if e is not None
                  and e.type == EventType.ERROR]
        assert errors and errors[-1].fatal


class TestTellingItWhatItIsHearing:
    """`language_hints` — a lever that exists, was never pulled, and does not
    help.

    The model appears to route through English whenever the target is not
    English: one Arabic paragraph through a phone microphone came back labelled
    ENGLISH, phonetically garbled into English words ("the movie you know, the
    one that ends on Saturday"), and the Hindi was translated from *that*.
    The same audio with `target=en` was correct Arabic and an accurate
    translation.

    `AudioTranscriptionConfig` accepts hints and we passed none, so the obvious
    cheap fix was to say "this is Arabic". It was tried on the S23 on
    2026-08-11: the hint reached the wire and the first utterance came back
    labelled English and garbled anyway.

    The plumbing is kept — it is correct, it costs nothing switched off, and it
    is one env var away when a future model revision honours it. These tests
    pin what we send, which is the part we control. They do NOT claim the hint
    works; it does not.
    """

    def test_by_default_we_say_nothing_and_let_it_detect(self) -> None:
        gw = _gw()
        assert gw.language_hints == []
        cfg = gw._build_config()
        assert cfg.input_audio_transcription.language_hints is None

    def test_a_hint_reaches_the_wire_when_one_is_configured(self) -> None:
        gw = GeminiTranslateGateway(target_language="hi", language_hints=["ar"])
        cfg = gw._build_config()
        hints = cfg.input_audio_transcription.language_hints
        assert hints is not None, "the hint was dropped on the floor"
        assert list(hints.language_codes) == ["ar"]

    def test_hints_and_auto_detection_are_never_sent_together(self) -> None:
        # The API rejects both at once; sending them would fail the connect and
        # surface as "translation is unavailable" with no clue why.
        gw = GeminiTranslateGateway(target_language="hi", language_hints=["ar"])
        in_tx = gw._build_config().input_audio_transcription
        assert in_tx.language_auto is None


class TestItDoesNotWaitForThePersonToStopTalking:
    """"Live" translation that starts when the speaker finishes is not live.

    Faraz's objection, and he is right: a 31-second paragraph read without
    pauses became ONE utterance, so nothing was translated until the reader
    stopped — eleven seconds after the first sentence had ended. An interpreter
    follows a sentence behind, not a paragraph behind.

    So an utterance now closes on a finished sentence as well as on silence.
    The cut is at the punctuation and nowhere else: cutting on silence alone is
    what used to chop sentences in half, and this must not bring that back.
    """

    async def test_a_finished_sentence_closes_without_any_pause(self) -> None:
        gw = _gw()
        # Delivered as deltas, back to back, exactly as the model sends them.
        for part in ("The old market in Cairo opens at nine",
                     " in the morning and closes at eleven at night."):
            await gw._handle_message(_msg(heard=part, heard_lang="en"))

        finals = [
            e for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final and e.role == "user"
        ]
        assert finals, "it waited for silence that never came"
        assert finals[0].text.endswith("night.")
        assert gw._heard_buf == "", "the buffer should start fresh"

    async def test_the_next_sentence_is_its_own_utterance(self) -> None:
        gw = _gw()
        await gw._handle_message(
            _msg(heard="The old market in Cairo opens at nine in the morning.",
                 heard_lang="en"))
        await _drain(gw)
        await gw._handle_message(
            _msg(heard=" My uncle Kareem sells spices there, and cinnamon.",
                 heard_lang="en"))

        finals = [
            e for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final and e.role == "user"
        ]
        assert finals, "the second sentence never closed"
        assert "Kareem" in finals[0].text
        assert "Cairo" not in finals[0].text, "the two ran together"

    async def test_it_does_not_cut_mid_thought(self) -> None:
        # The failure this must not reintroduce: "register account in all three
        # services and" / "generate" arriving as two lines.
        gw = _gw()
        for part in ("Register the account in all three services and",
                     " generate the keys"):
            await gw._handle_message(_msg(heard=part, heard_lang="en"))

        finals = [
            e for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final
        ]
        assert not finals, "an unfinished sentence was cut"

    async def test_two_words_and_a_full_stop_are_not_worth_a_round_trip(self) -> None:
        # "Yes." should ride along with what follows, not become its own
        # utterance with its own transcription, translation and voice.
        gw = _gw()
        await gw._handle_message(_msg(heard="Yes.", heard_lang="en"))
        finals = [
            e for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final
        ]
        assert not finals, "a two-word fragment was sent down the pipeline"

    async def test_an_endless_sentence_still_gets_broken_eventually(self) -> None:
        gw = _gw()
        await gw._handle_message(_msg(heard="x" * 500, heard_lang="en"))
        finals = [
            e for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final
        ]
        assert finals, "someone who never punctuates would never be translated"


class TestCuttingInsideTheBuffer:
    """The first attempt at sentence-cutting almost never fired.

    It asked "does the buffer END in a full stop", but deltas do not arrive on
    sentence boundaries — the model sends "…السبت. الايام هي" as one piece. So
    the check kept missing and a 31-second paragraph still came through as one
    block, which is the whole complaint.
    """

    async def test_a_sentence_buried_mid_delta_is_still_found(self) -> None:
        gw = _gw()
        await gw._handle_message(_msg(
            heard="The market opens at nine in the morning. My uncle sells",
            heard_lang="en"))

        finals = [
            e for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final and e.role == "user"
        ]
        assert finals, "the full stop in the middle was missed"
        assert finals[0].text == "The market opens at nine in the morning."

    async def test_the_words_after_it_are_kept_not_dropped(self) -> None:
        gw = _gw()
        await gw._handle_message(_msg(
            heard="The market opens at nine in the morning. My uncle sells",
            heard_lang="en"))
        await _drain(gw)
        assert gw._heard_buf == "My uncle sells", (
            f"the tail was lost or mangled: {gw._heard_buf!r}"
        )

    async def test_the_tail_joins_the_next_sentence(self) -> None:
        gw = _gw()
        await gw._handle_message(_msg(
            heard="The market opens at nine in the morning. My uncle",
            heard_lang="en"))
        await _drain(gw)
        await gw._handle_message(_msg(
            heard=" sells spices there, cumin and cinnamon.", heard_lang="en"))

        finals = [
            e for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final and e.role == "user"
        ]
        assert finals, "the second sentence never closed"
        assert finals[0].text == "My uncle sells spices there, cumin and cinnamon."

    async def test_several_sentences_in_one_delta_close_together(self) -> None:
        # Cutting only at the LAST ending keeps them in one line rather than
        # emitting a burst of fragments — one round trip, one spoken reply.
        gw = _gw()
        await gw._handle_message(_msg(
            heard="It opens at nine. It closes at eleven. My uncle",
            heard_lang="en"))
        finals = [
            e for e in await _drain(gw)
            if e.type == EventType.TRANSCRIPT and e.final and e.role == "user"
        ]
        assert len(finals) == 1
        assert finals[0].text == "It opens at nine. It closes at eleven."
        assert gw._heard_buf == "My uncle"
