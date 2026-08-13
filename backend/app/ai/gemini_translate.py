"""Gemini Live Translate adapter — continuous speech-to-speech translation.

A separate :class:`~app.ai.base.AIGateway` rather than a flag on
:mod:`app.ai.gemini`, because the contract is genuinely different: the translate
model takes **no tools, no system instruction, no text input and no video**.
Folding both into one class would produce an adapter that quietly accepts all of
them and silently ignores most, which is the kind of thing that looks like it
works until someone asks why the model never used a tool.

What it does take is one small block::

    translation_config = {target_language_code, echo_target_language}

The source language is **detected**, never configured — which is the whole
reason this is usable in a room where three languages are in play.

Audio formats line up with FarryOn's exactly (16 kHz PCM16 in, 24 kHz out), so
the entire capture and playback stack is reused unchanged.

Two behaviours to know before reading the code:

* With ``echo_target_language=False`` the model **stays silent** on speech that
  is already in the target language. That is correct, and it is also the single
  most confusing thing about the feature — the client is expected to explain the
  silence rather than wait for a translation that is never coming.
* Both transcriptions are enabled: ``input_audio_transcription`` is what was
  heard (tagged with the detected language) and ``output_audio_transcription``
  is the translation.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AIGateway, ToolSpec
from app.ai.events import (
    AudioChunkEvent,
    AudioEndEvent,
    AudioStartEvent,
    ErrorEvent,
    GatewayEvent,
    TranscriptEvent,
    TurnCompleteEvent,
)
from app.config import get_settings
from app.logging_conf import get_logger
from app.observability import metrics

logger = get_logger(__name__)

_INPUT_MIME = "audio/pcm;rate=16000"

#: How long the transcript must go quiet before the current utterance is
#: considered finished.
#:
#: The translate model NEVER sends ``turn_complete`` — measured against the real
#: model on 2026-08-10: 57 messages, not one turn boundary. It is a continuous
#: stream by nature, so the boundary has to be inferred. Without this nothing
#: ever finalises: the client shows every line as provisional forever, the
#: buffers grow for the whole session, and an hour of speech arrives as one
#: enormous paragraph.
#:
#: 1.2 s is long enough to ride out the gap between two clauses (the deltas
#: measured arrived ~0.6-1.0 s apart mid-sentence) and short enough that the
#: screen settles while the speaker is still in the room.
_UTTERANCE_GAP_S = 2.6

#: The shorter gap used when the text already ENDS in sentence punctuation.
#:
#: Waiting the full gap after a finished sentence just makes the screen feel
#: slow; waiting only this long after an unfinished one is what was chopping
#: sentences into fragments. So the wait depends on whether the sentence looks
#: finished, rather than being one compromise value for both.
_SENTENCE_GAP_S = 0.7

#: Characters that end a sentence, across the scripts this product is used in:
#: Latin, Devanagari danda, Arabic/Urdu question mark, CJK full stops.
_SENTENCE_ENDINGS = ".?!।॥؟。！？…"

#: Force a break in a monologue that never pauses, so the UI keeps moving and
#: one <p> does not grow without bound.
_MAX_UTTERANCE_CHARS = 400

#: The model pads its output stream with pure digital silence — 15.2 s of it
#: after 3.5 s of speech, and it keeps going for as long as the session is open
#: (measured 2026-08-10). Forwarding that is ~170 MB/hour of zeros over the
#: user's mobile data.
#:
#: A short run is still passed through, because a pause between sentences is
#: part of how the speech sounds; only a run longer than this is treated as
#: padding and dropped. Near-silence is deliberately NOT dropped — only
#: *exactly* zero samples, which is what the padding measured as. A quiet
#: passage that is merely faint is real audio and goes through.
_SILENCE_GRACE_CHUNKS = 4  # ~1 s at the 0.25 s chunks the model sends

#: How many upstream sockets one session may burn through in
#: :data:`_REOPEN_WINDOW_S`. The Live API ends a socket roughly every ten
#: minutes, so an hour of talking legitimately costs about six — spread out.
#: Six *in a row inside two minutes* is not a long conversation, it is a
#: failure that reconnecting cannot fix (a revoked key, a withdrawn model), and
#: retrying it forever would bill the account in silence.
_MAX_REOPENS = 6
_REOPEN_WINDOW_S = 120.0


class GeminiTranslateGateway(AIGateway):
    """Gemini Live Translate as an :class:`AIGateway`."""

    provider = "gemini_translate"

    def __init__(
        self,
        *,
        target_language: str = "en",
        echo_target_language: bool = False,
        model: str | None = None,
        system_prompt: str = "",
        tools: list[ToolSpec] | None = None,
        language_hints: list[str] | None = None,
        text_only: bool = False,
    ) -> None:
        settings = get_settings()
        super().__init__(
            system_prompt="",  # the translate model accepts none
            tools=[],  # nor any tools
            model=model or settings.gemini_translate_model,
        )
        self.target_language = target_language
        self.echo_target_language = echo_target_language
        # Ask for no spoken output at all. Audio out is six times the price of
        # audio in, so a caller that speaks for itself (the cascade path) or a
        # user reading captions should not be buying speech nobody hears.
        self.text_only = text_only
        # Falls back to the deployment-wide setting, so the lever can be pulled
        # without a client that knows about it.
        self.language_hints = (
            language_hints
            if language_hints is not None
            else list(settings.translate_language_hints)
        )
        self._api_key = settings.gemini_api_key
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._session: Any = None
        self._session_cm: Any = None
        self._recv_task: asyncio.Task[None] | None = None
        self._closed = False
        self._audio_open = False
        self._heard_buf = ""
        self._translated_buf = ""
        self._heard_lang: str | None = None
        # Time-to-first-translated-audio, measured from the last input
        # transcription delta. The number people actually feel.
        self._last_heard_at = 0.0
        # When the last transcript delta of EITHER side arrived — the clock the
        # utterance watchdog reads to decide the speaker has stopped.
        self._last_delta_at = 0.0
        # Consecutive all-zero audio chunks, for the silence-padding gate.
        self._silent_run = 0
        self._watchdog_task: asyncio.Task[None] | None = None
        # Set when the upstream announces it is about to close, cleared when the
        # replacement socket is up. See _reopen_upstream.
        self._goaway = False
        self._reopens = 0
        self._reopen_window_at = 0.0

    # -- Setup ---------------------------------------------------------------

    def _build_config(self) -> Any:
        from google.genai import types  # type: ignore[import-not-found]

        return types.LiveConnectConfig(
            response_modalities=["TEXT"] if self.text_only else ["AUDIO"],
            input_audio_transcription=self._input_transcription(types),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=self.target_language,
                echo_target_language=self.echo_target_language,
            ),
        )

    def _input_transcription(self, types: Any) -> Any:
        """How the model should decide what language it is hearing.

        With no hints it decides alone, which is how this shipped and is still
        the default. The lever exists because that decision is where the
        feature falls apart: one Arabic paragraph through a phone microphone
        with ``target=hi`` came back labelled ENGLISH, with English prose in
        the heard pane and invented sentences in the Hindi. The same audio with
        ``target=en`` was correct Arabic and an accurate translation. The model
        appears to pivot through English whenever the target is not English.

        Hints and auto-detection are mutually exclusive in the API, so only one
        of the two is ever sent.
        """
        if not self.language_hints:
            return types.AudioTranscriptionConfig()
        return types.AudioTranscriptionConfig(
            language_hints=types.LanguageHints(
                language_codes=list(self.language_hints)
            )
        )

    async def connect(self) -> None:
        """Open the translate session and start the receive loop.

        Raises:
            RuntimeError: If the SDK is missing, the key is unset, the SDK is
                too old to know ``translation_config``, or the model cannot be
                opened. Every one of those is answered as
                ``translate_unavailable`` by the session — never as a socket
                that dies without saying why.
        """
        try:
            from google import genai  # type: ignore[import-not-found]
            from google.genai import types  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "google-genai is not installed; `pip install google-genai`."
            ) from exc

        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        if not hasattr(types, "TranslationConfig"):
            # Better to say this plainly than to connect and translate nothing:
            # without the config the model would just be a very expensive echo.
            raise RuntimeError(
                "the installed google-genai has no TranslationConfig; "
                "upgrade the SDK to use live translation."
            )

        await self._open_upstream()
        self._last_delta_at = time.monotonic()
        self._recv_task = asyncio.create_task(self._receive_loop())
        self._watchdog_task = asyncio.create_task(self._utterance_watchdog())

    async def _open_upstream(self) -> None:
        """Open one upstream socket. Separate from :meth:`connect` because the
        socket is replaced mid-session — see :meth:`_reopen_upstream`."""
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]

        last_exc: Exception | None = None
        # v1alpha first: this model is a preview, and preview models live there.
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
                logger.info(
                    "gemini_translate.connected",
                    model=self.model,
                    api_version=api_version,
                    target=self.target_language,
                )
                return
            except Exception as exc:  # noqa: BLE001 - try the next channel
                last_exc = exc
                logger.warning(
                    "gemini_translate.connect_attempt_failed",
                    model=self.model,
                    api_version=api_version,
                    error=repr(exc),
                )

        raise RuntimeError(
            f"could not open {self.model!r} for translation; "
            f"last error: {last_exc!r}"
        )

    # -- Receive -------------------------------------------------------------

    async def _receive_loop(self) -> None:
        """Turn the provider stream into gateway events.

        ``session.receive()`` completes at the end of each turn, so it is
        re-entered in an outer loop — the same shape as ``gemini.py``, and for
        the same reason: without it the session would end after the first
        translated sentence.

        The upstream socket does not last a conversation. It is rolled over
        underneath this loop when it has to be; the caller never notices.
        """
        try:
            while not self._closed:
                saw_message = False
                reason: str | None = None
                try:
                    async for message in self._session.receive():
                        saw_message = True
                        await self._handle_message(message)
                        if self._goaway:
                            # Leave on our own terms, before it hangs up on us.
                            reason = "go_away"
                            break
                except asyncio.CancelledError:  # pragma: no cover
                    raise
                except Exception as exc:  # pragma: no cover - network/runtime
                    if self._closed:
                        break
                    reason = "upstream_error"
                    logger.warning(
                        "gemini_translate.upstream_error", error=str(exc)
                    )
                if self._closed:
                    break
                if reason is None and not saw_message:
                    reason = "stream_ended"
                if reason is None:
                    continue
                if await self._reopen_upstream(reason):
                    continue
                await self._queue.put(
                    ErrorEvent(
                        code="provider_error",
                        # Deliberately not `str(exc)`. What the upstream says
                        # here is a sentence about GoAway frames and policy
                        # violations, and it went to a user's screen once,
                        # truncated mid-word.
                        message=(
                            "The translation service ended the session and "
                            "could not be restarted."
                        ),
                        fatal=True,
                    )
                )
                break
        except asyncio.CancelledError:  # pragma: no cover - cancellation path
            raise
        finally:
            await self._queue.put(None)

    async def _reopen_upstream(self, reason: str) -> bool:
        """Replace the upstream socket without ending the user's session.

        The Live API caps how long one socket may live and sends a **GoAway**
        first, expecting the client to close and reconnect. We did not know the
        word: the upstream hung up at about ten minutes, and its raw protocol
        error was forwarded to the phone as a fatal one (device-seen
        2026-08-11, 9m43s in). Ten minutes does not cover a conversation, a
        meeting, or an appointment.

        Returns True if translation can carry on.
        """
        # Settle whatever the old socket had in flight. Those words were heard;
        # they should not be lost because the pipe underneath rolled over.
        with contextlib.suppress(Exception):
            await self._finalize(turn_complete=True)
        # And close the audio segment — the next socket starts a new one, and a
        # segment left open would splice two of them into one.
        if self._audio_open:
            self._audio_open = False
            await self._queue.put(AudioEndEvent())

        now = time.monotonic()
        if now - self._reopen_window_at > _REOPEN_WINDOW_S:
            self._reopen_window_at = now
            self._reopens = 0
        self._reopens += 1
        if self._reopens > _MAX_REOPENS:
            # Something is wrong that reconnecting cannot fix — a revoked key, a
            # withdrawn model. Retrying forever would bill for it in silence.
            logger.error(
                "gemini_translate.reopen_gave_up",
                reason=reason,
                attempts=self._reopens,
            )
            return False

        with contextlib.suppress(Exception):
            if self._session_cm is not None:
                await self._session_cm.__aexit__(None, None, None)
        self._session = None
        self._session_cm = None

        try:
            await self._open_upstream()
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            logger.error(
                "gemini_translate.reopen_failed", reason=reason, error=repr(exc)
            )
            return False

        self._goaway = False
        self._silent_run = 0
        self._last_delta_at = time.monotonic()
        metrics.TRANSLATE_UPSTREAM_REOPENS.inc()
        logger.info(
            "gemini_translate.reopened", reason=reason, attempts=self._reopens
        )
        return True

    async def _handle_message(self, message: Any) -> None:
        """Decode one server message into heard / translated / audio events."""
        # "I am about to hang up." Acting on it is the difference between a
        # clean rollover and the user reading a protocol error.
        go_away = getattr(message, "go_away", None)
        if go_away is not None:
            self._goaway = True
            logger.info(
                "gemini_translate.go_away",
                time_left=str(getattr(go_away, "time_left", None)),
            )
            return

        server_content = getattr(message, "server_content", None)
        if server_content is None:
            return

        model_turn = getattr(server_content, "model_turn", None)
        if model_turn is not None:
            for part in getattr(model_turn, "parts", []) or []:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    await self._handle_audio(data)

        # What was heard, in whatever language it was spoken. The text is a
        # DELTA, not a replacement — the model sends ' The meeting', ' will
        # start at' and so on, leading spaces included.
        in_tx = getattr(server_content, "input_transcription", None)
        in_text = getattr(in_tx, "text", None) if in_tx else None
        if in_text:
            self._last_heard_at = time.monotonic()
            self._last_delta_at = time.monotonic()
            self._heard_buf += in_text
            self._heard_lang = self._detected_lang(in_tx) or self._heard_lang
            await self._queue.put(
                TranscriptEvent(
                    role="user",
                    text=self._heard_buf,
                    final=False,
                    lang=self._heard_lang,
                )
            )

        # The translation, also a delta.
        out_tx = getattr(server_content, "output_transcription", None)
        out_text = getattr(out_tx, "text", None) if out_tx else None
        if out_text:
            self._last_delta_at = time.monotonic()
            self._translated_buf += out_text
            await self._queue.put(
                TranscriptEvent(
                    role="assistant",
                    text=self._translated_buf,
                    final=False,
                    # Reported by the model rather than assumed from config, so
                    # a target it silently substituted shows up as itself.
                    lang=self._detected_lang(out_tx) or self.target_language,
                )
            )

        # A monologue that never pauses would otherwise grow one line forever.
        if len(self._heard_buf) > _MAX_UTTERANCE_CHARS:
            await self._finalize(turn_complete=True)

        # Kept for completeness. Neither fired once against the real model, but
        # they cost nothing and a future revision may start sending them.
        if getattr(server_content, "interrupted", False):
            await self._finalize(turn_complete=False)
        elif getattr(server_content, "turn_complete", False):
            await self._finalize(turn_complete=True)

    async def _handle_audio(self, data: bytes) -> None:
        """Forward translated audio, dropping the model's silence padding."""
        if not data.strip(b"\x00"):
            self._silent_run += 1
            if self._silent_run > _SILENCE_GRACE_CHUNKS:
                # Padding, not a pause. Close the audio segment once, then stay
                # quiet until real sound returns.
                if self._audio_open:
                    self._audio_open = False
                    await self._queue.put(AudioEndEvent())
                return
        else:
            self._silent_run = 0

        if not self._audio_open:
            self._audio_open = True
            if self._last_heard_at > 0:
                latency = time.monotonic() - self._last_heard_at
                metrics.TRANSLATE_FIRST_AUDIO.observe(latency)
                logger.info(
                    "gemini_translate.first_audio",
                    latency_ms=int(latency * 1000),
                )
                self._last_heard_at = 0.0
            await self._queue.put(AudioStartEvent())
        await self._queue.put(AudioChunkEvent(pcm=data))

    async def _utterance_watchdog(self) -> None:
        """Close an utterance once the transcript goes quiet.

        This exists because the translate model never sends ``turn_complete``.
        Without it nothing is ever final: the client renders every line as
        provisional, and the buffers grow for the length of the session.
        """
        try:
            while not self._closed:
                await asyncio.sleep(0.25)
                if not (self._heard_buf or self._translated_buf):
                    continue
                if time.monotonic() - self._last_delta_at >= self._gap_needed():
                    await self._finalize(turn_complete=True)
        except asyncio.CancelledError:  # pragma: no cover - cancellation path
            raise
        except Exception as exc:  # noqa: BLE001
            # If this task dies, NOTHING is ever finalised again for the rest
            # of the session — the screen silently freezes into permanent grey
            # text. Far better to log it and keep the loop alive.
            logger.error("gemini_translate.watchdog_error", error=repr(exc))
            if not self._closed:
                self._watchdog_task = asyncio.create_task(
                    self._utterance_watchdog()
                )

    def _gap_needed(self) -> float:
        """How long the transcript must be quiet before this utterance closes.

        A speaker pausing to think is not the same as a speaker finishing, and
        one timeout cannot tell them apart — which is why a long sentence used
        to arrive as "register account in all three services and" / "generate"
        / "or time to register" / "account in", each its own card.

        So the punctuation decides. The model does emit it (measured: "Good
        morning, everyone." arrived with the full stop), and text that already
        ends a sentence needs only a short confirmation gap, while text that
        stops mid-clause is given real time to continue.
        """
        text = (self._heard_buf or self._translated_buf).rstrip()
        if text and text[-1] in _SENTENCE_ENDINGS:
            return _SENTENCE_GAP_S
        return _UTTERANCE_GAP_S

    @staticmethod
    def _detected_lang(transcription: Any) -> str | None:
        """The language the provider says it heard, if it says.

        Read defensively across a few plausible field names: this is a preview
        model, the field is not in the SDK's typed surface, and a missing
        language must degrade to "unlabelled" rather than break the stream.
        The client already treats ``None`` as "language unknown".
        """
        for attr in ("language_code", "language", "detected_language_code"):
            value = getattr(transcription, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()[:16]
        return None

    async def _finalize(self, *, turn_complete: bool) -> None:
        """Close out an utterance: end audio, finalize both transcripts.

        Two callers race here — the receive loop (on a length break) and the
        watchdog (on a quiet gap) — and they interleave at every ``await``. So
        the buffers are taken and cleared FIRST, synchronously, before anything
        is awaited. Clearing them afterwards let the other caller wake up in
        between and emit the same utterance a second time.
        """
        heard, self._heard_buf = self._heard_buf, ""
        heard_lang, self._heard_lang = self._heard_lang, None
        translated, self._translated_buf = self._translated_buf, ""
        was_open, self._audio_open = self._audio_open, False

        if not (heard or translated or was_open):
            return  # nothing to close — the other caller got here first

        if was_open:
            await self._queue.put(AudioEndEvent())
        if heard:
            await self._queue.put(
                TranscriptEvent(
                    role="user", text=heard, final=True, lang=heard_lang
                )
            )
        if translated:
            await self._queue.put(
                TranscriptEvent(
                    role="assistant",
                    text=translated,
                    final=True,
                    lang=self.target_language,
                )
            )
        if heard or translated:
            # The LANGUAGE, never the words. A question like "why did it say my
            # Arabic was English?" cannot be answered from a log that records
            # neither — and a language code is not content, so recording it
            # costs the speaker nothing. Lengths are there to tell a real
            # utterance from a stray fragment without quoting either.
            logger.info(
                "gemini_translate.utterance",
                heard_lang=heard_lang,
                target=self.target_language,
                heard_chars=len(heard),
                translated_chars=len(translated),
            )
        if turn_complete:
            await self._queue.put(TurnCompleteEvent())

    # -- Send ----------------------------------------------------------------

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        """Stream input audio to the translate session."""
        if self._session is None:
            return
        from google.genai import types  # type: ignore[import-not-found]

        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=_INPUT_MIME)
        )

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        """Ignored: the translate model has no vision."""
        return None

    async def send_text(self, text: str) -> None:
        """Ignored: the translate model takes no text input."""
        return None

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        """Ignored: the translate model exposes no tools."""
        return None

    async def events(self) -> AsyncIterator[GatewayEvent]:
        """Yield events until the session closes."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def interrupt(self) -> None:
        """Drop queued output.

        There is no barge-in to perform upstream: nobody is having a
        conversation with this model, and cancelling its generation would only
        clip a translation the room is still waiting to hear.
        """
        try:
            while True:
                self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    async def close(self) -> None:
        """Tear down the upstream session (idempotent)."""
        if self._closed:
            return
        # Close the utterance in flight BEFORE tearing anything down. Without
        # this the last thing anyone said is dropped: it was only ever sent as
        # a provisional line, so the screen would keep it greyed out forever
        # and the sentence that ended the conversation would be the one that
        # never settled.
        with contextlib.suppress(Exception):
            await self._finalize(turn_complete=True)
        self._closed = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._watchdog_task
            self._watchdog_task = None
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - teardown is best-effort
                logger.warning("gemini_translate.close_error", error=repr(exc))
        self._session = None
        self._session_cm = None
        await self._queue.put(None)
