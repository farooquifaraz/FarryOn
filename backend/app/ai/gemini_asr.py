"""Streaming speech recognition — the words as they are spoken.

This is step one of the translate cascade, and getting it wrong is what made
"live translation" not live.

**What came before, and why it failed.** Step one was the *translate* model,
asked for an English target on the theory that it would then write the source
language down faithfully. It did not: an Arabic paragraph came back as fluent
Vietnamese, with Egypt turned into America and the mosque into a church (S23,
2026-08-13); the day before, the same audio came back as English. Asked to
translate, it translated — into whatever it felt like — and every later step
inherited the answer.

Replacing it with **batch** transcription fixed the words but not the feel: a
whole utterance had to end before a single round trip could start, so nothing
was spoken until the speaker stopped. On a 31-second paragraph the first
translated word arrived eleven seconds *after* the last spoken one.

Trying to cut that up by slicing the AUDIO at sentence boundaries found in the
TEXT made it worse, and the reason is worth keeping: **the transcript lags the
audio by an unknown amount**, so a cut made where the text ends a sentence does
not land where the sound does. Chunks overlapped and words were split — the
screen showed the same clause twice and "عمي كريم" (my uncle Kareem) lost its
first half and came out as a woman named Reem.

**What this does instead.** A live session transcribes continuously — measured
at 77 deltas starting 3.4 s into a 31-second clip, in correct Arabic, with no
translation config anywhere near it. Because the words stream, nothing has to
be cut out of the audio at all: segmentation happens on TEXT ONLY, where
cutting is exact and reversible. That is the whole trick, and it is what the
simultaneous-translation literature does too (segment the hypothesis, not the
waveform).

The model is asked to stay silent. It is a listener; its own voice is nobody's
business here, and speech tokens cost six times what listening does.
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import math
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AIGateway, ToolSpec
from app.ai.events import (
    ErrorEvent,
    GatewayEvent,
    TranscriptEvent,
)
from app.config import get_settings
from app.logging_conf import get_logger
from app.observability import metrics

logger = get_logger(__name__)

#: How many times the upstream socket may be replaced inside
#: :data:`_REOPEN_WINDOW_S`. The Live API ends a socket roughly every ten
#: minutes, so a long conversation costs a handful over an hour. Six inside two
#: minutes is not a long conversation — it is a failure that reconnecting
#: cannot fix (a revoked key, a withdrawn model), and retrying forever would
#: bill for it in silence. The same numbers as the single-model path.
_MAX_REOPENS = 6
_REOPEN_WINDOW_S = 120.0

#: Audio that arrives while the socket is being replaced is kept, up to this
#: much, and sent to the new one. A rollover takes about half a second and
#: people do not stop talking for it; five seconds of 16 kHz mono PCM16.
_REOPEN_AUDIO_CAP_BYTES = 5 * 32000

# -- When the upstream is told someone is speaking -----------------------------
#
# The model's own speech detector is off (see `_build_config`), so the listening
# window is ours to open and close. All of it is measured in seconds of AUDIO,
# not of clock, so a recording replayed fast behaves like the room it was made in.

#: A 40 ms chunk at or above this (RMS of PCM16) counts as sound worth hearing.
#: Low on purpose: a phone in a quiet room sits near 100 and speech across a
#: table is 800 and up. Missing a quiet speaker is the worse mistake.
_SOUND_RMS = 250

#: How long it must be quiet before the window is closed. With it left open
#: over silence the model writes things nobody said: a replayed 25 seconds of
#: nothing produced Thai syllables and "[noise]" (2026-10-02).
_QUIET_CLOSE_S = 2.0

#: A window is closed and reopened at the first quiet chunk after this long...
_WINDOW_SOFT_S = 8.0
#: ...and at this long whatever the sound is doing. One window held open for
#: a whole session goes deaf after a long silence: the second of two identical
#: English clips, 25 seconds apart, came back as a single stray word. Renewed
#: every few seconds it came back whole.
_WINDOW_HARD_S = 15.0

#: Text for a sentence keeps arriving for a moment after its last sound. Past
#: this long after the sound stopped, with the window closed, it is not that.
_LATE_TEXT_S = 3.0

#: What the recogniser writes when it hears something that is not speech.
_NOISE_TAG = re.compile(r"[\[<(]\s*noise\s*[\]>)]", re.IGNORECASE)


def _rms(pcm: bytes) -> float:
    """Loudness of one chunk of PCM16."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(v * v for v in samples) / len(samples))

#: Characters that end a sentence, across the scripts this product is used in.
_SENTENCE_ENDINGS = ".?!।॥؟。！？…"

#: Shortest run of text that may be closed off, so "Yes." does not become an
#: utterance with its own translation and its own voice. Measured in the units
#: below, not in characters — see `_length_units`.
_MIN_SENTENCE_UNITS = 25

#: Break a speaker who never punctuates, so the screen keeps moving.
_MAX_UTTERANCE_UNITS = 260

#: What one character of a dense script is worth in Latin characters. Chinese
#: writes a whole word where English writes a syllable, and our own logs put
#: the ratio at 3.2 (20 characters heard became 68, 26 became 105, 30 became
#: 89). Three is that number rounded down.
#:
#: Without this the thresholds above are nonsense outside the Latin and Indic
#: scripts they were tuned on. A Chinese sentence rarely reaches 25 characters,
#: so it could never be closed on its own full stop; and 25 characters of
#: Chinese is a paragraph, so nothing was ever held back either. Both failures
#: were visible in one run (S23, 2026-08-14).
_DENSE_SCRIPT_WEIGHT = 3.0

#: How long the transcript must be quiet before an unfinished thought is closed
#: anyway. Long enough to ride out the gap between two clauses.
_QUIET_GAP_S = 1.8

#: The same, for a fragment too short to be a sentence. A scrap like "50" or a
#: lone Chinese character is far more likely to be the beginning of something
#: than a complete thought, so it gets a longer benefit of the doubt before we
#: give up and send it on its own.
_SHORT_QUIET_GAP_S = 4.5


def _is_dense(ch: str) -> bool:
    """True for scripts that pack a word into a character or two."""
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF  # kana
        or 0x3400 <= o <= 0x4DBF  # CJK extension A
        or 0x4E00 <= o <= 0x9FFF  # CJK unified ideographs
        or 0xAC00 <= o <= 0xD7AF  # Hangul syllables
        or 0xF900 <= o <= 0xFAFF  # CJK compatibility
    )


def _length_units(text: str) -> float:
    """How long this text is, in Latin characters' worth of meaning."""
    return sum(_DENSE_SCRIPT_WEIGHT if _is_dense(ch) else 1.0 for ch in text)

_SILENT_INSTRUCTION = (
    "You are a silent transcriber. Never speak, never answer, never comment, "
    "never translate. Produce no output of any kind. You exist only so that "
    "the incoming audio is transcribed."
)


class GeminiStreamingASR(AIGateway):
    """A live session used purely as a speech recogniser."""

    provider = "gemini_asr"

    def __init__(
        self,
        *,
        model: str | None = None,
        system_prompt: str = "",
        tools: list[ToolSpec] | None = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            system_prompt="",
            tools=[],
            model=model or settings.translate_asr_model,
        )
        self._api_key = settings.gemini_api_key
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._session: Any = None
        self._session_cm: Any = None
        self._recv_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._closed = False
        self._buf = ""
        self._lang: str | None = None
        #: Sentences closed so far. Translation is asynchronous, so each one
        #: has to be nameable — otherwise the client cannot tell which
        #: translation belongs to which sentence, and the first one loses its
        #: translation to the second (device-seen 2026-08-14).
        self._utterances = 0
        self._last_delta_at = 0.0
        #: Counted, not forwarded. If it ever rises the silence instruction has
        #: stopped working and we are paying for speech nobody hears. The byte
        #: total is what turns that into a number of seconds, and therefore
        #: into money — a chunk count alone never told anyone what it cost.
        self._spoke_anyway = 0
        self._spoke_bytes = 0
        #: The upstream said it is about to hang up (see `_reopen_upstream`).
        self._goaway = False
        self._reopens = 0
        self._reopens_total = 0
        self._reopen_window_at = 0.0
        #: Set while the socket is being replaced; audio waits in `_held`.
        self._reopening = False
        self._held: list[bytes] = []
        self._held_bytes = 0
        #: For the closing summary — the only record of what a session did.
        self._connected_at = 0.0
        self._audio_bytes = 0
        #: The listening window (see `_keep_window`). Positions are in seconds
        #: of audio received.
        self._window_open = False
        self._window_since = 0.0
        self._sound_at = -1e9

    # -- Setup ---------------------------------------------------------------

    def _build_config(self) -> Any:
        from google.genai import types

        return types.LiveConnectConfig(
            # TEXT is rejected by every live model that transcribes well, so the
            # audio reply is accepted and thrown away. Re-checked against
            # gemini-2.5-flash-native-audio-latest on 2026-08-14: still
            # rejected, with "the requested combination of response modalities
            # (TEXT) is not supported by the model".
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=_SILENT_INSTRUCTION,
            # The instruction above is not obeyed. Every session of
            # 2026-08-14 produced audio we discarded and paid for — 1075
            # chunks in the worst one — and speech is billed at several times
            # what listening costs. Since the modality cannot be turned off,
            # the budget is taken away instead.
            #
            # Measured against real speech: with and without this cap the
            # transcript came back character-for-character identical, so it
            # costs us nothing we want. What is NOT yet proven is that it
            # removes the waste, because the model declined to speak during
            # that experiment at all — `gemini_asr.model_spoke` in a real
            # session is the number to watch.
            max_output_tokens=1,
            # The model's own speech detector is switched off; the listening
            # window is opened and closed here instead (see `_keep_window`).
            # Left on, it decides when "someone started speaking" — and for
            # speech that is already running when the session opens, or that
            # never pauses (a video, a lecture, a room of people), it never
            # decides: the session stays on "Listening" with nothing written
            # for as long as the user cares to wait. Three sessions of
            # 2026-10-02 took 89, 27 and 58 seconds of loud Mandarin and wrote
            # nothing. Replayed, the same 24 seconds gave 0 characters with the
            # detector on, 42 (after a 17-second wait) with its sensitivity
            # raised, and 138 from second 2.7 with it off.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=True
                )
            ),
        )

    async def _mark(self, session: Any, *, start: bool) -> None:
        """Open or close the listening window on this socket. Best effort: a
        socket that refuses is found out by the receive loop."""
        from google.genai import types

        with contextlib.suppress(Exception):
            if start:
                await session.send_realtime_input(
                    activity_start=types.ActivityStart()
                )
            else:
                await session.send_realtime_input(activity_end=types.ActivityEnd())

    async def _keep_window(self, session: Any, pcm: bytes) -> None:
        """Decide, before this chunk is sent, whether the upstream is listening.

        Open on sound; close after two quiet seconds; and renew a window that
        has been open a while, at a quiet chunk if one comes and regardless if
        not. The chunk that opens a window is sent inside it, so the first
        syllable is not left outside.
        """
        now = self._audio_bytes / 32000
        loud = _rms(pcm) >= _SOUND_RMS
        if loud:
            self._sound_at = now
        if not self._window_open:
            if loud:
                await self._mark(session, start=True)
                self._window_open, self._window_since = True, now
            return
        if now - self._sound_at >= _QUIET_CLOSE_S:
            await self._mark(session, start=False)
            self._window_open = False
            return
        age = now - self._window_since
        if age >= _WINDOW_HARD_S or (age >= _WINDOW_SOFT_S and not loud):
            await self._mark(session, start=False)
            await self._mark(session, start=True)
            self._window_since = now

    async def connect(self) -> None:
        await self._open_upstream()
        self._connected_at = self._last_delta_at = time.monotonic()
        self._recv_task = asyncio.create_task(self._receive_loop())
        self._watchdog_task = asyncio.create_task(self._quiet_watchdog())

    async def _open_upstream(self) -> None:
        """Open one live socket. Called at the start, and again at each
        rollover — see :meth:`_reopen_upstream`."""
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError("google-genai is not installed.") from exc
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")

        last: Exception | None = None
        for api_version in ("v1alpha", "v1beta"):
            try:
                client = genai.Client(
                    api_key=self._api_key,
                    http_options=types.HttpOptions(api_version=api_version),
                )
                cm = client.aio.live.connect(
                    model=self.model, config=self._build_config()
                )
                self._session = await cm.__aenter__()
                self._session_cm = cm
                # A new socket knows nothing of the old one's window.
                self._window_open = False
                logger.info(
                    "gemini_asr.connected", model=self.model, api_version=api_version
                )
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                logger.warning(
                    "gemini_asr.connect_attempt_failed",
                    model=self.model,
                    api_version=api_version,
                    error=repr(exc),
                )
        raise RuntimeError(f"could not open {self.model!r}; last error: {last!r}")

    # -- Sending -------------------------------------------------------------

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        if self._closed:
            return
        self._audio_bytes += len(pcm)
        session = self._session
        if self._reopening or session is None:
            # The socket is being replaced. Keep what is said meanwhile — the
            # oldest goes first if the wait outlasts the cap.
            self._held.append(pcm)
            self._held_bytes += len(pcm)
            while self._held_bytes > _REOPEN_AUDIO_CAP_BYTES and len(self._held) > 1:
                self._held_bytes -= len(self._held.pop(0))
            return
        await self._keep_window(session, pcm)
        await self._send_pcm(session, pcm)

    async def _send_pcm(self, session: Any, pcm: bytes) -> None:
        from google.genai import types

        with contextlib.suppress(Exception):
            await session.send_realtime_input(
                audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
            )

    async def send_text(self, text: str) -> None:
        return None

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        return None

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        return None

    # -- Receiving -----------------------------------------------------------

    async def _receive_loop(self) -> None:
        """Read the upstream until the session is closed.

        The upstream socket does not last a conversation. It is replaced
        underneath this loop when it has to be; the caller never notices.
        """
        try:
            while not self._closed:
                saw = False
                reason: str | None = None
                try:
                    async for message in self._session.receive():
                        saw = True
                        await self._handle(message)
                        if self._goaway:
                            # Leave on our own terms, before it hangs up on us.
                            reason = "go_away"
                            break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    if self._closed:
                        break
                    reason = "upstream_error"
                    logger.warning("gemini_asr.stream_error", error=str(exc))
                if self._closed:
                    break
                if reason is None and not saw:
                    reason = "stream_ended"
                if reason is None:
                    continue
                if await self._reopen_upstream(reason):
                    continue
                await self._queue.put(
                    ErrorEvent(
                        code="provider_error",
                        # Never `str(exc)`: what the upstream says here is a
                        # sentence about GoAway frames, and it went to a
                        # user's screen once, cut off mid-word.
                        message=(
                            "Speech recognition stopped and could not be "
                            "restarted. Start translation again."
                        ),
                        fatal=True,
                    )
                )
                break
        except asyncio.CancelledError:
            raise
        finally:
            await self._queue.put(None)

    async def _reopen_upstream(self, reason: str) -> bool:
        """Replace the upstream socket without ending the user's session.

        The Live API caps how long one socket may live and sends a **GoAway**
        first, expecting the client to close and reconnect. The single-model
        path learned this on 2026-08-11 (it died 9m43s in); this recogniser
        replaced that path two days later and never inherited the fix, so a
        cascade session ended at about ten minutes with "Speech recognition
        stopped unexpectedly" — whatever TRANSLATE_MAX_SESSION_SECONDS said.

        The text heard so far is ours, not the socket's: the open utterance
        stays in the buffer and carries on, sentence numbers keep counting,
        and audio spoken during the swap is held and sent to the new socket.

        Returns True if recognition can carry on.
        """
        now = time.monotonic()
        if now - self._reopen_window_at > _REOPEN_WINDOW_S:
            self._reopen_window_at = now
            self._reopens = 0
        self._reopens += 1
        if self._reopens > _MAX_REOPENS:
            logger.error(
                "gemini_asr.reopen_gave_up", reason=reason, attempts=self._reopens
            )
            return False

        held: list[bytes] = []
        self._reopening = True
        try:
            old_cm, self._session, self._session_cm = self._session_cm, None, None
            with contextlib.suppress(Exception):
                if old_cm is not None:
                    await old_cm.__aexit__(None, None, None)
            try:
                await self._open_upstream()
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                logger.error(
                    "gemini_asr.reopen_failed", reason=reason, error=repr(exc)
                )
                return False
            self._goaway = False
            held, self._held, self._held_bytes = self._held, [], 0
            for pcm in held:
                await self._send_pcm(self._session, pcm)
        finally:
            self._reopening = False

        self._reopens_total += 1
        metrics.TRANSLATE_UPSTREAM_REOPENS.inc()
        logger.info(
            "gemini_asr.reopened",
            reason=reason,
            attempts=self._reopens,
            held_audio_s=round(sum(len(p) for p in held) / 32000, 2),
        )
        return True

    async def _handle(self, message: Any) -> None:
        # "I am about to hang up." Acting on it is the difference between a
        # clean rollover and a session that dies at ten minutes.
        go_away = getattr(message, "go_away", None)
        if go_away is not None:
            self._goaway = True
            logger.info(
                "gemini_asr.go_away",
                time_left=str(getattr(go_away, "time_left", None)),
            )
            return

        content = getattr(message, "server_content", None)
        if content is None:
            return

        # It was told not to speak. Count it if it does — that is money.
        turn = getattr(content, "model_turn", None)
        if turn is not None:
            for part in getattr(turn, "parts", []) or []:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None)
                if data:
                    self._spoke_anyway += 1
                    self._spoke_bytes += len(data)

        tx = getattr(content, "input_transcription", None)
        text = getattr(tx, "text", None) if tx else None
        if text:
            # "[noise]" is the recogniser saying it heard no words. It went on
            # screen as a sentence and on to the translator as one.
            text = _NOISE_TAG.sub("", text)
        if not text:
            return
        if (
            self._audio_bytes
            and not self._window_open
            and self._audio_bytes / 32000 - self._sound_at > _LATE_TEXT_S
        ):
            # Words that arrive long after the room went quiet were not said
            # in it. Closing a window over its silent tail is answered, now and
            # then, with a stray syllable in a language nobody spoke — "ครับ"
            # six seconds after the last sound of an English sentence.
            return
        self._last_delta_at = time.monotonic()
        self._buf += text
        code = getattr(tx, "language_code", None)
        if code:
            self._lang = code
        await self._queue.put(
            TranscriptEvent(
                role="user",
                text=self._buf,
                final=False,
                lang=self._lang,
                utterance=self._utterances,
            )
        )

        # Segmentation happens HERE and only here — on the text. The audio is
        # never cut, which is what stopped words being split in half.
        cut = self._last_sentence_end()
        if cut is not None and _length_units(self._buf[:cut]) >= _MIN_SENTENCE_UNITS:
            await self._close(cut, "sentence_end")
        elif _length_units(self._buf) > _MAX_UTTERANCE_UNITS:
            await self._close(len(self._buf), "too_long")

    def _last_sentence_end(self) -> int | None:
        """Where the last sentence ends, or None if none has yet.

        A full stop is not always a full stop. In "4.9 billion" the point
        between the digits is a decimal separator, and cutting there turned one
        number into two sentences — "more than 4" and then "9 billion", both
        translated and both wrong (device-seen in English and in Arabic,
        2026-08-14).

        Two shapes are refused. A point with digits on both sides is never an
        ending. A point with a digit before it and *nothing yet after it* is
        left alone as well: the transcript arrives a few characters at a time,
        so "…more than 4." is what "4.9" looks like a moment before the 9
        lands. Refusing to decide costs one delta, and the quiet watchdog will
        close the utterance anyway if the speaker really did stop on a number.
        """
        for i in range(len(self._buf) - 1, -1, -1):
            ch = self._buf[i]
            if ch not in _SENTENCE_ENDINGS:
                continue
            if ch == "." and i > 0 and self._buf[i - 1].isdigit():
                after = self._buf[i + 1 :]
                if not after or after[0].isdigit():
                    continue  # a decimal point, or too early to tell
            return i + 1
        return None

    async def _close(self, cut: int, why: str = "sentence_end") -> None:
        """Emit everything up to `cut` as final; keep the rest for next time."""
        head, self._buf = self._buf[:cut].strip(), self._buf[cut:].lstrip()
        if not head:
            return
        closed = self._utterances
        self._utterances += 1
        # Lengths and the reason, never the words. Without this line the log
        # showed translations and nothing else: a 39-second gap in a live
        # session (2026-10-01) could have been a quiet room, a held microphone
        # or a deaf recogniser, and nothing recorded could tell them apart.
        logger.info(
            "gemini_asr.utterance",
            n=closed,
            chars=len(head),
            why=why,
            at_s=round(time.monotonic() - self._connected_at, 1)
            if self._connected_at
            else 0.0,
        )
        await self._queue.put(
            TranscriptEvent(
                role="user",
                text=head,
                final=True,
                lang=self._lang,
                utterance=closed,
            )
        )

    async def _quiet_watchdog(self) -> None:
        """Close an unfinished thought once the speaker has clearly stopped.

        Someone who trails off without a full stop would otherwise never be
        translated at all.

        This used to close whatever was in the buffer, however little that was
        — the one path that ignored `_MIN_SENTENCE_UNITS` entirely. In Latin
        script the omission hid, because 1.8 seconds of speech is a good many
        characters. Chinese exposed it: single characters went out as their own
        utterances, and 此外 ("besides") lost its first half and was translated
        as 外 ("outside"). A short fragment now waits considerably longer, on
        the reasoning that a scrap is usually the start of a sentence rather
        than all of one.
        """
        try:
            while not self._closed:
                await asyncio.sleep(0.25)
                if not self._buf:
                    continue
                short = _length_units(self._buf) < _MIN_SENTENCE_UNITS
                gap = _SHORT_QUIET_GAP_S if short else _QUIET_GAP_S
                if time.monotonic() - self._last_delta_at >= gap:
                    await self._close(len(self._buf), "pause")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("gemini_asr.watchdog_error", error=repr(exc))
            if not self._closed:
                self._watchdog_task = asyncio.create_task(self._quiet_watchdog())

    # -- Lifecycle -----------------------------------------------------------

    async def events(self) -> AsyncIterator[GatewayEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def interrupt(self) -> None:
        return None

    async def close(self) -> None:
        if self._closed:
            return
        with contextlib.suppress(Exception):
            if self._buf:
                await self._close(len(self._buf), "session_end")
        self._closed = True
        logger.info(
            "gemini_asr.summary",
            seconds=round(time.monotonic() - self._connected_at, 1)
            if self._connected_at
            else 0.0,
            # 16 kHz mono PCM16 in, which is 32000 bytes a second.
            audio_s=round(self._audio_bytes / 32000, 1),
            utterances=self._utterances,
            reopens=self._reopens_total,
        )
        if self._spoke_anyway:
            logger.warning(
                "gemini_asr.model_spoke",
                chunks=self._spoke_anyway,
                bytes=self._spoke_bytes,
                # 24 kHz mono PCM16 out, which is 48000 bytes a second.
                seconds=round(self._spoke_bytes / 48000, 1),
            )
        for task in (self._watchdog_task, self._recv_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._watchdog_task = self._recv_task = None
        if self._session_cm is not None:
            with contextlib.suppress(Exception):
                await self._session_cm.__aexit__(None, None, None)
        self._session = self._session_cm = None
        await self._queue.put(None)
