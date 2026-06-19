"""Unit tests for the binary frame codec (``app.ws.frames``)."""

from __future__ import annotations

import struct

import pytest

from app.ws.frames import (
    HEADER_SIZE,
    FrameTag,
    decode_frame,
    encode_frame,
    now_ms,
)


def test_header_size_is_nine() -> None:
    assert HEADER_SIZE == 9


def test_roundtrip_input_audio() -> None:
    payload = b"\x01\x02\x03\x04abc"
    ts = 1_718_764_800_123
    frame = encode_frame(FrameTag.INPUT_AUDIO, payload, ts=ts)
    tag, decoded_ts, decoded_payload = decode_frame(frame)
    assert tag == FrameTag.INPUT_AUDIO
    assert decoded_ts == ts
    assert decoded_payload == payload


def test_roundtrip_all_tags() -> None:
    for tag in (FrameTag.INPUT_AUDIO, FrameTag.INPUT_VIDEO, FrameTag.OUTPUT_AUDIO):
        frame = encode_frame(tag, b"data", ts=42)
        out_tag, out_ts, out_payload = decode_frame(frame)
        assert out_tag == tag
        assert out_ts == 42
        assert out_payload == b"data"


def test_header_layout_is_little_endian() -> None:
    """byte0 = tag (uint8), bytes1..8 = uint64 LE timestamp (PROTOCOL.md §2)."""
    ts = 0x0102030405060708
    frame = encode_frame(0x03, b"", ts=ts)
    assert len(frame) == HEADER_SIZE
    assert frame[0] == 0x03
    # Little-endian uint64 of ts in bytes 1..8.
    assert frame[1:9] == struct.pack("<Q", ts)


def test_empty_payload_roundtrip() -> None:
    frame = encode_frame(FrameTag.OUTPUT_AUDIO, b"", ts=0)
    tag, ts, payload = decode_frame(frame)
    assert tag == FrameTag.OUTPUT_AUDIO
    assert ts == 0
    assert payload == b""


def test_default_timestamp_is_populated() -> None:
    before = now_ms()
    frame = encode_frame(FrameTag.INPUT_AUDIO, b"x")
    _, ts, _ = decode_frame(frame)
    after = now_ms()
    assert before <= ts <= after


def test_decode_rejects_short_frame() -> None:
    with pytest.raises(ValueError):
        decode_frame(b"\x01\x02")  # fewer than 9 bytes


def test_encode_rejects_out_of_range_tag() -> None:
    with pytest.raises(ValueError):
        encode_frame(256, b"x", ts=0)


def test_unknown_tag_decodes_without_error() -> None:
    """Forward-compatibility: unknown tags decode to a plain int."""
    frame = encode_frame(0x7F, b"payload", ts=1)
    tag, _, payload = decode_frame(frame)
    assert tag == 0x7F
    assert payload == b"payload"
