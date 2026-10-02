"""The streaming recogniser: what it emits, and when.

This step has now been wrong three different ways on a real device, and each
one is pinned below:

1. It was the translate model with an English target. Asked to translate, it
   translated — one Arabic paragraph came back as fluent Vietnamese, another as
   English, with Egypt turned into America and the mosque into a church.
2. Replaced with batch transcription, the words were right but nothing was
   spoken until the speaker stopped: eleven seconds of silence after a
   31-second paragraph.
3. Cutting the AUDIO at sentence boundaries found in the TEXT made it worse.
   The transcript lags the sound by an unknown amount, so the cuts landed in
   the wrong places: the same clause appeared twice and "عمي كريم" lost its
   first half, arriving as a woman named Reem.

The fix for all three is here: a recogniser that streams, and segmentation that
touches only the text. The audio is never cut, so (3) cannot come back.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from app.ai.events import EventType
from app.ai.gemini_asr import (
    _MAX_UTTERANCE_UNITS,
    _MIN_SENTENCE_UNITS,
    GeminiStreamingASR,
)


def _msg(text: str | None = None, lang: str | None = None,
         audio: bytes | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=(
                SimpleNamespace(text=text, language_code=lang)
                if text is not None
                else None
            ),
            model_turn=(
                SimpleNamespace(
                    parts=[SimpleNamespace(inline_data=SimpleNamespace(data=audio))]
                )
                if audio is not None
                else None
            ),
        )
    )


def _asr() -> GeminiStreamingASR:
    return GeminiStreamingASR(model="fake")


async def _drain(asr: GeminiStreamingASR) -> list:
    out = []
    while not asr._queue.empty():
        out.append(asr._queue.get_nowait())
    return out


def _finals(events: list) -> list:
    return [e for e in events if e.type == EventType.TRANSCRIPT and e.final]


class TestWordsArriveWhileSomeoneIsStillTalking:
    async def test_deltas_are_shown_before_anything_is_final(self) -> None:
        # The screen has to move while the sentence is still being spoken —
        # that is the difference between live and a recording.
        asr = _asr()
        for part in ("السوق القديم", " في القاهره"):
            await asr._handle(_msg(part, "ar"))

        events = await _drain(asr)
        assert events, "nothing was shown at all"
        assert all(not e.final for e in events)
        assert events[-1].text == "السوق القديم في القاهره"
        assert events[-1].lang == "ar"

    async def test_a_finished_sentence_closes_at_once(self) -> None:
        asr = _asr()
        await asr._handle(
            _msg("السوق القديم في القاهره بيفتح الساعه 9 الصبح.", "ar"))

        finals = _finals(await _drain(asr))
        assert finals, "it waited for a pause that had not come"
        assert finals[0].text.endswith(".")

    async def test_a_sentence_buried_mid_delta_is_found(self) -> None:
        # The model does not send text on sentence boundaries: it sends
        # "…الساعه 9 الصبح. عمي كريم" as one piece.
        asr = _asr()
        await asr._handle(
            _msg("السوق بيفتح الساعه 9 الصبح. عمي كريم بيبيع", "ar"))

        finals = _finals(await _drain(asr))
        assert finals
        assert finals[0].text == "السوق بيفتح الساعه 9 الصبح."
        assert asr._buf == "عمي كريم بيبيع", "the tail was lost"

    async def test_the_tail_becomes_the_next_sentence_whole(self) -> None:
        # The exact failure that produced "Reem": a name split across two
        # utterances. The tail has to be carried, not dropped or duplicated.
        asr = _asr()
        await asr._handle(_msg("السوق بيفتح الساعه 9 الصبح. عمي كريم", "ar"))
        await _drain(asr)
        await asr._handle(_msg(" بيبيع توابل هناك، كمون وقرفه.", "ar"))

        finals = _finals(await _drain(asr))
        assert finals
        assert finals[0].text == "عمي كريم بيبيع توابل هناك، كمون وقرفه."

    async def test_nothing_is_ever_repeated(self) -> None:
        asr = _asr()
        await asr._handle(_msg("It opens at nine in the morning. My uncle", "en"))
        first = _finals(await _drain(asr))
        await asr._handle(_msg(" sells spices there, cumin and cinnamon.", "en"))
        second = _finals(await _drain(asr))

        assert first and second
        assert "opens at nine" not in second[0].text, (
            f"the first sentence came round again: {second[0].text!r}"
        )

    async def test_an_unfinished_thought_is_not_cut(self) -> None:
        asr = _asr()
        await asr._handle(_msg("Register the account in all three services and", "en"))
        assert not _finals(await _drain(asr))

    async def test_two_words_and_a_full_stop_wait_for_company(self) -> None:
        asr = _asr()
        await asr._handle(_msg("Yes.", "en"))
        assert not _finals(await _drain(asr))
        assert len("Yes.") < _MIN_SENTENCE_UNITS

    async def test_someone_who_never_punctuates_is_still_translated(self) -> None:
        asr = _asr()
        await asr._handle(_msg("x" * (_MAX_UTTERANCE_UNITS + 10), "en"))
        assert _finals(await _drain(asr)), "they would never be heard from"


class TestANumberIsNotASentence:
    """A decimal point looks exactly like a full stop.

    On the device this turned "4.9 billion" into two utterances — "more than
    4" and "9 billion" — each translated on its own and each saying something
    false. Seen in English and again in Arabic (2026-08-14).
    """

    async def test_a_decimal_point_does_not_end_the_sentence(self) -> None:
        asr = _asr()
        await asr._handle(_msg("Every day more than 4.9 billion people log in.", "en"))
        finals = _finals(await _drain(asr))

        assert finals
        assert finals[0].text == "Every day more than 4.9 billion people log in."

    async def test_it_waits_rather_than_guess_at_a_trailing_point(self) -> None:
        # The transcript arrives a few characters at a time, so "4.9" is "4."
        # for a moment. Deciding then is deciding too early.
        asr = _asr()
        await asr._handle(_msg("Every day more than 4.", "en"))
        assert not _finals(await _drain(asr))

        await asr._handle(_msg("9 billion people log in.", "en"))
        finals = _finals(await _drain(asr))
        assert finals
        assert finals[0].text == "Every day more than 4.9 billion people log in."

    async def test_a_sentence_may_still_end_on_a_number(self) -> None:
        # The rule above must not swallow a real ending. Here the point is
        # followed by more words, which settles it.
        asr = _asr()
        await asr._handle(
            _msg("The total for the whole of last year was 49. Then it fell", "en")
        )
        finals = _finals(await _drain(asr))

        assert finals
        assert finals[0].text == "The total for the whole of last year was 49."


class TestScriptsThatPackAWordIntoACharacter:
    """Counting characters is not counting language.

    Chinese broke the thresholds in both directions at once: a complete
    sentence of ten characters was too short to ever be closed, while single
    characters went out on their own. 此外 ("besides") lost its first half and
    was translated as 外 ("outside") — device-seen 2026-08-14.
    """

    async def test_a_short_chinese_sentence_is_long_enough(self) -> None:
        asr = _asr()
        await asr._handle(_msg("不斷的獲取數字信息。", "zh"))
        finals = _finals(await _drain(asr))

        assert finals, "a whole sentence was held back for being 'too short'"
        assert finals[0].text == "不斷的獲取數字信息。"

    async def test_a_lone_character_waits_for_the_rest_of_its_word(self) -> None:
        asr = _asr()
        await asr._handle(_msg("如今,我们的世界高度依赖科技和全球通信。此", "zh"))
        await _drain(asr)
        assert asr._buf == "此", "the tail was not carried"

        await asr._handle(_msg("外,約75%的商業溝通", "zh"))
        assert asr._buf.startswith("此外"), (
            f"besides became outside: {asr._buf!r}"
        )

    async def test_a_pause_does_not_send_a_scrap_on_its_own(self) -> None:
        # The watchdog was the one path that ignored the minimum entirely.
        # A pause in the middle of a word is what split 此外.
        asr = _asr()
        asr._buf = "此"
        asr._last_delta_at = time.monotonic() - 2.5  # past the ordinary gap
        watchdog = asyncio.create_task(asr._quiet_watchdog())
        await asyncio.sleep(0.4)

        assert not _finals(await _drain(asr)), "a scrap went out alone"

        asr._last_delta_at = time.monotonic() - 10.0  # the speaker really stopped
        await asyncio.sleep(0.4)
        assert _finals(await _drain(asr)), "it was never sent at all"

        watchdog.cancel()


class TestItIsAListenerNotASpeaker:
    async def test_the_models_own_audio_is_counted_and_dropped(self) -> None:
        # It is told to stay silent. If it speaks anyway we are paying for
        # audio nobody hears, so it is counted — but never forwarded.
        asr = _asr()
        await asr._handle(_msg(audio=b"\x01\x02" * 100))

        assert asr._spoke_anyway == 1
        assert not [
            e for e in await _drain(asr) if e.type != EventType.TRANSCRIPT
        ]

    async def test_a_message_with_nothing_in_it_is_harmless(self) -> None:
        asr = _asr()
        await asr._handle(SimpleNamespace(server_content=None))
        await asr._handle(_msg(None))
        assert await _drain(asr) == []


class TestTheLanguageItReports:
    async def test_it_carries_the_detected_language_through(self) -> None:
        asr = _asr()
        await asr._handle(_msg("السوق القديم في القاهره بيفتح بدري.", "ar-EG"))
        finals = _finals(await _drain(asr))
        assert finals[0].lang == "ar-EG"

    async def test_a_delta_without_one_keeps_the_last_known(self) -> None:
        asr = _asr()
        await asr._handle(_msg("السوق القديم", "ar-EG"))
        await asr._handle(_msg(" في القاهره بيفتح بدري.", None))
        finals = _finals(await _drain(asr))
        assert finals[0].lang == "ar-EG"


class _FakeSocket:
    """One upstream live socket: plays its script, then does what it is told."""

    def __init__(self, script: list, *, then: str = "wait") -> None:
        self._script = list(script)
        self._then = then
        self.sent: list[bytes] = []
        #: Everything sent, in order: "audio", "start", "end".
        self.marks: list[str] = []
        self.done = asyncio.Event()

    async def receive(self):
        script, self._script = self._script, []
        for message in script:
            yield message
        self.done.set()
        if self._then == "raise":
            raise RuntimeError("1008 None. Connection aborted because the client")
        if self._then == "wait":
            await asyncio.sleep(3600)

    async def send_realtime_input(
        self, *, audio=None, activity_start=None, activity_end=None
    ) -> None:
        if audio is not None:
            self.sent.append(audio.data)
            self.marks.append("audio")
        if activity_start is not None:
            self.marks.append("start")
        if activity_end is not None:
            self.marks.append("end")


def _go_away() -> SimpleNamespace:
    return SimpleNamespace(go_away=SimpleNamespace(time_left="5s"), server_content=None)


async def _connected(sockets: list[_FakeSocket], *, fail_reopen: bool = False):
    """A recogniser whose upstream is the given sockets, in order."""
    asr = _asr()
    opened: list[_FakeSocket] = []

    async def open_upstream() -> None:
        if opened and fail_reopen:
            raise RuntimeError("the key was revoked")
        sock = sockets[len(opened)]
        opened.append(sock)
        asr._session = sock

    asr._open_upstream = open_upstream  # type: ignore[method-assign]
    await asr.connect()
    return asr, opened


async def _until(predicate, *, limit: float = 2.0) -> None:
    deadline = time.monotonic() + limit
    while not predicate():
        assert time.monotonic() < deadline, "it never happened"
        await asyncio.sleep(0.01)


class TestTheSocketDoesNotLastAConversation:
    """The Live API hangs up at about ten minutes and says so first.

    The single-model path was taught this on 2026-08-11. This recogniser
    replaced it two days later and never inherited the lesson, so a cascade
    session could not outlive ten minutes whatever the configured hour said.
    """

    async def test_a_go_away_is_answered_with_a_new_socket(self) -> None:
        first = _FakeSocket([_msg("The old market in Cairo", "en"), _go_away()])
        second = _FakeSocket([_msg(" opens at nine in the morning.", "en")])
        asr, opened = await _connected([first, second])
        try:
            await _until(lambda: second.done.is_set())
            events = await _drain(asr)
            assert opened == [first, second]
            assert not [e for e in events if e.type == EventType.ERROR]
            # The sentence that was open across the swap arrives whole: the
            # text heard so far is ours, not the socket's.
            finals = _finals(events)
            assert [e.text for e in finals] == [
                "The old market in Cairo opens at nine in the morning."
            ]
        finally:
            await asr.close()

    async def test_sentence_numbers_carry_on_across_the_swap(self) -> None:
        # The phone files each translation under its sentence's number. A new
        # socket that counted from zero again would overwrite the first card.
        first = _FakeSocket(
            [_msg("The old market in Cairo opens at nine.", "en"), _go_away()])
        second = _FakeSocket([_msg("My uncle Kareem sells spices there.", "en")])
        asr, _ = await _connected([first, second])
        try:
            await _until(lambda: second.done.is_set())
            finals = _finals(await _drain(asr))
            assert [e.utterance for e in finals] == [0, 1]
        finally:
            await asr.close()

    async def test_a_socket_that_dies_without_warning_is_replaced_too(self) -> None:
        first = _FakeSocket([_msg("hello", "en")], then="raise")
        second = _FakeSocket([])
        asr, opened = await _connected([first, second])
        try:
            await _until(lambda: second.done.is_set())
            assert opened == [first, second]
            assert not [
                e for e in await _drain(asr) if e.type == EventType.ERROR
            ]
        finally:
            await asr.close()

    async def test_what_is_said_during_the_swap_reaches_the_new_socket(self) -> None:
        asr, _ = await _connected([_FakeSocket([])])
        try:
            asr._reopening = True
            await asr.send_audio(b"\x01" * 640)
            await asr.send_audio(b"\x02" * 640)
            assert asr._held == [b"\x01" * 640, b"\x02" * 640]

            # A new socket, and the swap finishes the way `_reopen_upstream`
            # finishes it.
            fresh = _FakeSocket([])

            async def open_upstream() -> None:
                asr._session = fresh

            asr._open_upstream = open_upstream  # type: ignore[method-assign]
            assert await asr._reopen_upstream("go_away")
            assert fresh.sent == [b"\x01" * 640, b"\x02" * 640]
            assert asr._held == []
        finally:
            await asr.close()

    async def test_when_it_cannot_reopen_the_user_reads_a_sentence(self) -> None:
        first = _FakeSocket([], then="raise")
        asr, _ = await _connected([first], fail_reopen=True)
        try:
            await _until(lambda: not asr._queue.empty())
            errors = [e for e in await _drain(asr) if e and e.type == EventType.ERROR]
            assert len(errors) == 1 and errors[0].fatal
            # Written for a person. The upstream's own words are about GoAway
            # frames, and they reached a screen once, cut off mid-word.
            assert "1008" not in errors[0].message
            assert "GoAway" not in errors[0].message
        finally:
            await asr.close()

    async def test_reopening_forever_is_not_recovery(self) -> None:
        # A revoked key reconnects and dies, reconnects and dies. That would
        # bill in silence; after a handful inside two minutes it stops.
        from app.ai.gemini_asr import _MAX_REOPENS

        sockets = [_FakeSocket([], then="raise") for _ in range(_MAX_REOPENS + 2)]
        asr, opened = await _connected(sockets)
        try:
            await _until(
                lambda: any(
                    getattr(e, "type", None) == EventType.ERROR
                    for e in list(asr._queue._queue)  # type: ignore[attr-defined]
                )
            )
            assert len(opened) == _MAX_REOPENS + 1
        finally:
            await asr.close()


_LOUD = bytes([0, 16]) * 640  # 40 ms at an RMS of 4096
_QUIET = bytes([16, 0]) * 640  # 40 ms at an RMS of 16


class TestTheListeningWindowIsOurs:
    """The model's own speech detector is off, because it could not be trusted.

    Three sessions on 2026-10-02 took 89, 27 and 58 seconds of loud Mandarin —
    a video already playing when the session opened — and wrote nothing: the
    detector never decided that anyone had started. The app said "Listening".
    """

    def test_the_detector_is_switched_off(self) -> None:
        config = _asr()._build_config()
        assert config.realtime_input_config.automatic_activity_detection.disabled

    async def test_sound_opens_the_window_before_it_is_sent(self) -> None:
        sock = _FakeSocket([])
        asr, _ = await _connected([sock])
        try:
            await asr.send_audio(_QUIET)
            await asr.send_audio(_LOUD)
            # Quiet goes up unmarked; the first loud chunk goes up INSIDE the
            # window, so the opening syllable is not left outside it.
            assert sock.marks == ["audio", "start", "audio"]
        finally:
            await asr.close()

    async def test_two_quiet_seconds_close_it(self) -> None:
        # Left open over silence the model writes things nobody said.
        sock = _FakeSocket([])
        asr, _ = await _connected([sock])
        try:
            await asr.send_audio(_LOUD)
            for _ in range(60):  # 2.4 s
                await asr.send_audio(_QUIET)
            assert sock.marks.count("end") == 1
            assert not asr._window_open
            await asr.send_audio(_LOUD)
            assert sock.marks[-2:] == ["start", "audio"]
        finally:
            await asr.close()

    async def test_a_window_open_too_long_is_renewed(self) -> None:
        # One window held for a whole session goes deaf after a long silence;
        # speech that never pauses still gets a new one.
        from app.ai.gemini_asr import _WINDOW_HARD_S

        sock = _FakeSocket([])
        asr, _ = await _connected([sock])
        try:
            for _ in range(int((_WINDOW_HARD_S + 1) * 25)):
                await asr.send_audio(_LOUD)
            assert sock.marks.count("start") == 2
            assert sock.marks.count("end") == 1
            assert asr._window_open
        finally:
            await asr.close()

    async def test_a_new_socket_gets_its_own_window(self) -> None:
        first = _FakeSocket([])
        asr, _ = await _connected([first])
        try:
            await asr.send_audio(_LOUD)
            fresh = _FakeSocket([])

            async def open_upstream() -> None:
                asr._session = fresh
                asr._window_open = False

            asr._open_upstream = open_upstream  # type: ignore[method-assign]
            assert await asr._reopen_upstream("go_away")
            await asr.send_audio(_LOUD)
            assert fresh.marks == ["start", "audio"]
        finally:
            await asr.close()

    async def test_noise_is_not_a_sentence(self) -> None:
        asr = _asr()
        await asr._handle(_msg("[noise]", "en"))
        await asr._handle(_msg(" <noise> ", "en"))
        assert not [e for e in await _drain(asr) if e.text.strip()]

    async def test_words_long_after_the_room_went_quiet_are_dropped(self) -> None:
        sock = _FakeSocket([])
        asr, _ = await _connected([sock])
        try:
            await asr.send_audio(_LOUD)
            for _ in range(150):  # six quiet seconds
                await asr.send_audio(_QUIET)
            await asr._handle(_msg("ครับ", "th"))
            assert asr._buf == ""
            # Text that follows its sound closely is the sentence itself.
            await asr.send_audio(_LOUD)
            await asr._handle(_msg("Hello there", "en"))
            assert asr._buf == "Hello there"
        finally:
            await asr.close()
