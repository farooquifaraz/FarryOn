"""Keep a copy of the microphone audio a session received, as a WAV file.

A measuring instrument, not a feature. The question it exists to settle:
when a word arrives at the transcriber wrong at its very start — "Teach" heard
as "Reach", "Wife" as "life" (2026-09-07) — was the start of that word in the
audio the phone sent, or was it already gone? A transcript cannot say; the
recording can. Off unless ``Settings.debug_audio_dump_dir`` names a directory,
and never on in production.

It must never cost the session anything: every failure is logged and
swallowed, and a dump that cannot be written is simply not written.
"""

from __future__ import annotations

import wave
from pathlib import Path

from app.logging_conf import get_logger

logger = get_logger(__name__)

#: The mic stream's format: 16 kHz, mono, 16-bit PCM (PROTOCOL.md).
_SAMPLE_RATE = 16_000
_CHANNELS = 1
_SAMPLE_WIDTH = 2


class AudioDump:
    """Append incoming PCM to ``<dir>/<session_id>.wav``; close on session end."""

    def __init__(self, directory: str, session_id: str) -> None:
        self._path = Path(directory) / f"{session_id}.wav" if directory else None
        self._wav: wave.Wave_write | None = None
        self._failed = False
        self.bytes_written = 0

    @property
    def enabled(self) -> bool:
        return self._path is not None and not self._failed

    def write(self, pcm: bytes) -> None:
        """Append one frame. Opens the file on the first frame, so a session
        that never sends audio leaves nothing behind."""
        if not self.enabled or not pcm:
            return
        try:
            if self._wav is None:
                assert self._path is not None
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._wav = wave.open(str(self._path), "wb")
                self._wav.setnchannels(_CHANNELS)
                self._wav.setsampwidth(_SAMPLE_WIDTH)
                self._wav.setframerate(_SAMPLE_RATE)
                logger.info("audio_dump.opened", path=str(self._path))
            self._wav.writeframes(pcm)
            self.bytes_written += len(pcm)
        except Exception as exc:  # noqa: BLE001 - diagnostics never break audio
            self._failed = True
            logger.warning("audio_dump.failed", error=repr(exc))
            self._close_quietly()

    def close(self) -> None:
        if self._wav is not None:
            seconds = self.bytes_written / (_SAMPLE_RATE * _SAMPLE_WIDTH)
            logger.info(
                "audio_dump.closed",
                path=str(self._path),
                seconds=round(seconds, 1),
            )
        self._close_quietly()

    def _close_quietly(self) -> None:
        wav, self._wav = self._wav, None
        if wav is not None:
            try:
                wav.close()
            except Exception:  # noqa: BLE001
                pass
