"""The audio dump records exactly what arrived, and costs a session nothing.

It exists to answer one question about a wrong transcript — did the start of
the word ever reach us? — so it must be a faithful copy, and it must be unable
to hurt the session it is watching.
"""

from __future__ import annotations

import wave

from app.ws.audio_dump import AudioDump


def test_writes_a_playable_wav_of_exactly_the_bytes_fed(tmp_path) -> None:
    dump = AudioDump(str(tmp_path), "sess1")
    first = bytes(range(0, 200, 2)) * 2
    second = b"\x01\x02" * 400
    dump.write(first)
    dump.write(second)
    dump.close()

    with wave.open(str(tmp_path / "sess1.wav"), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16_000
        assert w.readframes(w.getnframes()) == first + second


def test_off_when_no_directory_is_configured(tmp_path) -> None:
    dump = AudioDump("", "sess2")
    dump.write(b"\x00\x01" * 100)
    dump.close()
    assert not dump.enabled
    assert list(tmp_path.iterdir()) == []


def test_a_silent_session_leaves_no_file(tmp_path) -> None:
    """Opened on the first frame, so no audio means no empty WAV to sift."""
    dump = AudioDump(str(tmp_path), "sess3")
    dump.close()
    assert not (tmp_path / "sess3.wav").exists()


def test_an_unwritable_directory_never_raises(tmp_path) -> None:
    """Diagnostics that could take the mic down are worse than none."""
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    dump = AudioDump(str(blocker), "sess4")
    dump.write(b"\x00\x01" * 100)  # must not raise
    dump.write(b"\x00\x01" * 100)  # nor on the retry
    dump.close()
    assert not dump.enabled


def test_creates_the_directory_it_was_given(tmp_path) -> None:
    target = tmp_path / "deeper" / "still"
    dump = AudioDump(str(target), "sess5")
    dump.write(b"\x00\x01" * 10)
    dump.close()
    assert (target / "sess5.wav").exists()
