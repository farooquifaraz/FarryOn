"""In-process per-session rate limit for outbound message sends.

Stops a runaway loop or a mis-fire from blasting many messages in seconds. Like
``idempotency``, it is per-process and in-memory — meant to catch the common
case, not to be a distributed quota.
"""

from __future__ import annotations

import time
from collections import defaultdict

#: Window length and max sends allowed within it, per session.
_WINDOW_SECONDS = 60.0
_MAX_PER_WINDOW = 8

_hits: dict[str, list[float]] = defaultdict(list)


def allow(session_id: str | None) -> bool:
    """Record a send and return whether it's within the rate limit."""
    key = session_id or "_anon"
    now = time.monotonic()
    recent = [t for t in _hits[key] if now - t < _WINDOW_SECONDS]
    if len(recent) >= _MAX_PER_WINDOW:
        _hits[key] = recent
        return False
    recent.append(now)
    _hits[key] = recent
    return True
