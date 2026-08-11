"""Translation in three visible steps: hear, translate, speak.

The single-model path (:mod:`app.ai.gemini_translate`) does all three at once
and, for a non-English target, gets them wrong. Feeding it one Arabic paragraph
through a phone microphone with ``target=hi`` produced a transcript labelled
ENGLISH, the Arabic phonetically mangled into English words — *"the movie you
know, the one that ends on Saturday"* — and Hindi translated from that mangling,
including two plain factual errors and sentences nobody said. The same audio
with ``target=en`` came back as correct Arabic and an accurate translation
(S23, 2026-08-11). Telling it what language it was hearing did not help;
`language_hints` reached the wire and changed nothing.

So the job is split, which is also what most production translators do:

===========  ==========================================  ==========
step         who                                         measured
===========  ==========================================  ==========
hear         the live model with ``target=en``, which     accurate
             transcribes the source language correctly
translate    a text model, reasoning switched off         0.6 s
speak        a streaming TTS model                        first
                                                          sound in
                                                          1.5 s
===========  ==========================================  ==========

That is about two seconds more than the single-model path — on top of the 2.6 s
this feature already waits for a speaker to finish a sentence, so it is a
smaller change than it sounds.

What it buys, beyond the translation being right: **every stage is visible.**
When the old path turned Arabic into "the movie", nothing in our logs could say
which step broke. Here the source text and the translation are separate values.
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
    EventType,
    GatewayEvent,
    TranscriptEvent,
)
from app.config import get_settings
from app.logging_conf import get_logger
from app.observability import metrics

logger = get_logger(__name__)

#: The listening step is asked to translate into English because that is the
#: configuration measured to transcribe the source language correctly. We do not
#: use its English — only the source transcript that comes with it.
_LISTEN_TARGET = "en"


class CascadeTranslateGateway(AIGateway):
    """Hear with one model, translate with a second, speak with a third."""

    provider = "cascade_translate"

    def __init__(
        self,
        *,
        target_language: str = "en",
        echo_target_language: bool = False,
        listener: AIGateway | None = None,
        translator: Any | None = None,
        speaker: Any | None = None,
        model: str | None = None,
        system_prompt: str = "",
        tools: list[ToolSpec] | None = None,
    ) -> None:
        settings = get_settings()
        super().__init__(system_prompt="", tools=[], model=model or "cascade")
        self.target_language = target_language
        self.echo_target_language = echo_target_language
        self._settings = settings
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._closed = False
        self._pump_task: asyncio.Task[None] | None = None
        self._work: set[asyncio.Task[None]] = set()

        if listener is None:
            from app.ai.gemini_translate import GeminiTranslateGateway

            listener = GeminiTranslateGateway(
                target_language=_LISTEN_TARGET,
                echo_target_language=False,
                text_only=True,  # we speak for ourselves; do not buy its audio
            )
        self._listener = listener
        self._translator = translator or _GeminiTextTranslator(settings)
        self._speaker = speaker or _GeminiSpeaker(settings)

    # -- Lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        await self._listener.connect()
        self._pump_task = asyncio.create_task(self._pump_listener())

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        await self._listener.send_audio(pcm)

    async def send_text(self, text: str) -> None:
        """Ignored: a translator has no chat channel."""
        return None

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        """Ignored: the translate path never sees the camera."""
        return None

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        """Ignored: no tools here."""
        return None

    async def events(self) -> AsyncIterator[GatewayEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def interrupt(self) -> None:
        with contextlib.suppress(asyncio.QueueEmpty):
            while True:
                self._queue.get_nowait()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in list(self._work):
            task.cancel()
        with contextlib.suppress(Exception):
            await self._listener.close()
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump_task
        await self._queue.put(None)

    # -- Step 1: hear --------------------------------------------------------

    async def _pump_listener(self) -> None:
        """Forward the source transcript, and start a translation for each
        finished utterance.

        Only the *heard* side is forwarded. The listening model's own English is
        discarded — it exists because asking for an English target is what makes
        it transcribe the source correctly, not because anyone wants to read it.
        """
        try:
            async for event in self._listener.events():
                if self._closed:
                    break
                if event.type == EventType.ERROR:
                    await self._queue.put(event)
                    continue
                if event.type != EventType.TRANSCRIPT:
                    continue
                assert isinstance(event, TranscriptEvent)
                if event.role != "user":
                    continue
                await self._queue.put(event)
                if event.final and event.text.strip():
                    task = asyncio.create_task(
                        self._translate_and_speak(event.text, event.lang)
                    )
                    self._work.add(task)
                    task.add_done_callback(self._work.discard)
        except asyncio.CancelledError:
            raise
        finally:
            # Let anything already in flight finish speaking — the last sentence
            # of a conversation is the one people wait for.
            if self._work:
                await asyncio.wait(list(self._work), timeout=20)
            await self._queue.put(None)

    # -- Steps 2 and 3: translate, then speak --------------------------------

    async def _translate_and_speak(self, text: str, source_lang: str | None) -> None:
        if self._same_language(source_lang):
            # Already in the target. Saying it back is what
            # `echo_target_language=False` avoids upstream; the client shows its
            # own explanation for the silence.
            return
        try:
            started = time.monotonic()
            translated = await self._translator.translate(
                text, source_lang=source_lang, target=self.target_language
            )
        except Exception as exc:  # noqa: BLE001 - one utterance, not the session
            logger.error("cascade_translate.translate_failed", error=repr(exc))
            metrics.TRANSLATE_ERRORS.labels(stage="translate").inc()
            return
        if not translated.strip() or self._closed:
            return
        logger.info(
            "cascade_translate.translated",
            source_lang=source_lang,
            target=self.target_language,
            source_chars=len(text),
            translated_chars=len(translated),
            ms=int((time.monotonic() - started) * 1000),
        )
        await self._queue.put(
            TranscriptEvent(
                role="assistant",
                text=translated,
                final=True,
                lang=self.target_language,
            )
        )
        await self._speak(translated)

    async def _speak(self, text: str) -> None:
        opened = False
        first_at: float | None = None
        started = time.monotonic()
        try:
            async for chunk in self._speaker.stream(text, self.target_language):
                if self._closed:
                    break
                if not chunk:
                    continue
                if not opened:
                    opened = True
                    first_at = time.monotonic() - started
                    metrics.TRANSLATE_FIRST_AUDIO.observe(first_at)
                    await self._queue.put(AudioStartEvent())
                await self._queue.put(AudioChunkEvent(pcm=chunk))
        except Exception as exc:  # noqa: BLE001 - the text is already on screen
            logger.error("cascade_translate.speak_failed", error=repr(exc))
            metrics.TRANSLATE_ERRORS.labels(stage="speak").inc()
        finally:
            if opened:
                await self._queue.put(AudioEndEvent())
                logger.info(
                    "cascade_translate.spoke",
                    first_audio_ms=int((first_at or 0) * 1000),
                )

    def _same_language(self, source_lang: str | None) -> bool:
        if not source_lang or self.echo_target_language:
            return False
        return (
            source_lang.split("-")[0].lower()
            == self.target_language.split("-")[0].lower()
        )


class _GeminiTextTranslator:
    """Step two: text in, text out.

    Reasoning is switched **off**. It is not free: the same sentence took 7.2 s
    with thinking on and 0.6 s with it off, and a translation is not a problem
    that benefits from deliberation.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def translate(
        self, text: str, *, source_lang: str | None, target: str
    ) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._settings.gemini_api_key)
        source = source_lang or "the source language"
        prompt = (
            f"Translate the following {source} text into {target}.\n"
            "Reply with the translation only — no preamble, no notes, no "
            "quotation marks, no alternatives. Keep names, numbers, times and "
            "places exactly as they are. If the text is a fragment, translate "
            "the fragment; do not complete it.\n\n"
            f"{text}"
        )
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=self._settings.translate_text_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return (response.text or "").strip()


class _GeminiSpeaker:
    """Step three: text in, 24 kHz PCM16 out, streamed.

    Streaming is not a nicety. The non-streaming model returns one blob after
    8.2 s; the streaming one starts after 1.5 s and produces 6.6 s of speech in
    3.3 s, so once it starts it stays ahead of playback and never stutters.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def stream(self, text: str, target: str) -> AsyncIterator[bytes]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._settings.gemini_api_key)
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self._settings.translate_tts_voice
                    )
                )
            ),
        )
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def produce() -> None:
            try:
                for response in client.models.generate_content_stream(
                    model=self._settings.translate_tts_model,
                    contents=text,
                    config=config,
                ):
                    for candidate in response.candidates or []:
                        content = getattr(candidate, "content", None)
                        for part in getattr(content, "parts", None) or []:
                            inline = getattr(part, "inline_data", None)
                            data = getattr(inline, "data", None)
                            if data:
                                queue.put_nowait(data)
            finally:
                queue.put_nowait(None)

        worker = asyncio.create_task(asyncio.to_thread(produce))
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await worker
