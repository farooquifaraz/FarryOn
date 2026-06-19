"""Binary media frame codec for ``/ws/live``.

Implements the fixed little-endian header defined in ``PROTOCOL.md`` section 2::

     offset  size  field
       0      1    tag   (uint8)   — stream type
       1      8    ts    (uint64)  — capture/emit time, ms since epoch (LE)
       9      ..   payload         — raw bytes (PCM or JPEG)

WebSocket frames are already length-delimited, so no length prefix is required.
"""

from __future__ import annotations

import struct
import time
from enum import IntEnum

#: Size of the fixed binary header in bytes (1-byte tag + 8-byte LE uint64 ts).
HEADER_SIZE: int = 9

#: ``struct`` format for the header: little-endian uint8 + uint64.
_HEADER_STRUCT = struct.Struct("<BQ")


class FrameTag(IntEnum):
    """Stream type tags for binary frames (see ``PROTOCOL.md``)."""

    INPUT_AUDIO = 0x01  # client -> server, PCM16 LE 16 kHz mono
    INPUT_VIDEO = 0x02  # client -> server, JPEG single frame
    OUTPUT_AUDIO = 0x03  # server -> client, PCM16 LE 24 kHz mono


def now_ms() -> int:
    """Return the current wall-clock time in milliseconds since the epoch."""
    return int(time.time() * 1000)


def encode_frame(tag: int | FrameTag, payload: bytes, ts: int | None = None) -> bytes:
    """Encode a media payload into a wire frame.

    Args:
        tag: The :class:`FrameTag` (or raw ``uint8``) identifying the stream.
        payload: Raw media bytes (PCM samples or a JPEG frame).
        ts: Timestamp in ms since epoch. Defaults to :func:`now_ms`.

    Returns:
        The header (9 bytes) followed by ``payload``.

    Raises:
        ValueError: If ``tag`` or ``ts`` does not fit the header field widths.
    """
    tag_int = int(tag)
    if not 0 <= tag_int <= 0xFF:
        raise ValueError(f"tag must fit in a uint8, got {tag_int}")
    timestamp = now_ms() if ts is None else int(ts)
    if not 0 <= timestamp <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"ts must fit in a uint64, got {timestamp}")
    return _HEADER_STRUCT.pack(tag_int, timestamp) + bytes(payload)


def decode_frame(data: bytes) -> tuple[int, int, bytes]:
    """Decode a wire frame into ``(tag, ts, payload)``.

    Args:
        data: A full binary frame as received from the socket.

    Returns:
        A tuple of ``(tag, ts_ms, payload_bytes)``. ``tag`` is returned as a
        plain ``int`` so unknown/forward-compatible tags do not raise; callers
        may coerce to :class:`FrameTag` when they expect a known value.

    Raises:
        ValueError: If ``data`` is shorter than the fixed header.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(
            f"frame too short: need >= {HEADER_SIZE} bytes, got {len(data)}"
        )
    tag, ts = _HEADER_STRUCT.unpack_from(data, 0)
    payload = bytes(data[HEADER_SIZE:])
    return tag, ts, payload
