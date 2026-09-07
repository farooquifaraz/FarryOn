"""A second reading of what the user said, from a model that reads better.

The Live API's native-audio transcriber is what the app shows the user, and on
English words from a Hindi speaker it is the weakest link in the chain: the
same 18 seconds of microphone audio (2026-09-07) came back as ``ऑल ब्यूटीफुल
वाइल्ड`` from it and ``Call beautiful wife.`` from ``gemini-2.5-flash`` reading
the identical bytes; a plain English sentence landed in Telugu script. The
model that ANSWERS hears the raw audio and mostly gets it right — only the
words on screen are wrong, and those are what a person judges the app by.

So the audio of each turn is read a second time, off the critical path, and
the bubble is corrected when the better reading lands. Nothing waits on it:
the reply has already started by the time this is asked, and a failure or a
timeout simply leaves the first reading in place.
"""

from __future__ import annotations

import asyncio
import io
import wave

from app.logging_conf import get_logger

logger = get_logger(__name__)

_SAMPLE_RATE = 16_000

#: Shorter than this is a cough, not a sentence — nothing to refine.
MIN_AUDIO_SECONDS = 0.3

#: Longer than this is kept from the END: the most recent words are the ones
#: on screen, and a runaway buffer must not become a runaway bill.
MAX_AUDIO_SECONDS = 30.0

_PROMPT = (
    "Transcribe this recording verbatim. Write each word in the script the "
    "speaker is using: Latin for English words, Devanagari for Hindi words, "
    "and likewise for any other language. Keep the speaker's own mix of "
    "languages exactly as spoken. Do not translate, correct, summarise, or "
    "add anything. Output only the transcript, on one line, with no quotes "
    "or labels. If there are no words, output nothing."
)


def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw 16 kHz mono PCM16 in a WAV container, in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def clip_to_budget(pcm: bytes) -> bytes:
    """Keep at most :data:`MAX_AUDIO_SECONDS` of the most recent audio."""
    limit = int(MAX_AUDIO_SECONDS * _SAMPLE_RATE * 2)
    return pcm[-limit:] if len(pcm) > limit else pcm


def seconds_of(pcm: bytes) -> float:
    return len(pcm) / (_SAMPLE_RATE * 2)


async def refine_transcript(
    pcm: bytes,
    *,
    api_key: str,
    model: str,
    timeout_s: float,
    client_factory=None,
) -> str | None:
    """Return a better transcript of ``pcm``, or None if there is nothing
    to say or the reading did not arrive in time.

    Never raises: this is a correction to something already on screen, and
    the worst outcome is the first reading standing.
    """
    if seconds_of(pcm) < MIN_AUDIO_SECONDS or not api_key:
        return None
    pcm = clip_to_budget(pcm)
    try:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]

        client = (client_factory or (lambda: genai.Client(api_key=api_key)))()

        def _call():
            return client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(
                        data=pcm_to_wav(pcm), mime_type="audio/wav"
                    ),
                    _PROMPT,
                ],
            )

        response = await asyncio.wait_for(asyncio.to_thread(_call), timeout_s)
        # What this reading cost, per call, so the bill is a measurement and
        # not an estimate: audio in (the turn), text out (one line).
        usage = getattr(response, "usage_metadata", None)
        logger.info(
            "transcript.refine_usage",
            model=model,
            audio_s=round(seconds_of(pcm), 1),
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            total_tokens=getattr(usage, "total_token_count", None),
        )
        text = (getattr(response, "text", None) or "").strip()
        # One line, as asked; a model that adds a second line is trimmed to
        # the first rather than trusted.
        text = text.splitlines()[0].strip() if text else ""
        return text or None
    except asyncio.TimeoutError:
        logger.info("transcript.refine_timeout", model=model, timeout_s=timeout_s)
        return None
    except Exception as exc:  # noqa: BLE001 - a correction must never break a turn
        logger.warning("transcript.refine_failed", model=model, error=repr(exc))
        return None
