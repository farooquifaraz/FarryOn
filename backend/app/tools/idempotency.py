"""In-process idempotency guard for outward sends (FarryOn Product UX Spec §3.4).

``send_email`` and the ``send_telegram`` bot path are the only tools that
*actually deliver* a message server-side (the WhatsApp/SMS/Telegram-deep-link
paths just open an app for the user to tap send, so they can't double-send). For
those two, a retried turn — the model re-issuing the same send, or a reconnect
replaying a turn — could deliver the SAME message twice, which is exactly the
kind of thing that makes a product feel broken.

This module keeps a tiny time-boxed set of recently-sent fingerprints. Before a
real send the tool checks :func:`already_sent`; after a successful send it calls
:func:`mark_sent`. A duplicate within the TTL window short-circuits and reports
success without sending again.

Scope/limits (documented honestly):
  * Per-process, in-memory — it does NOT survive a worker restart, and across
    multiple workers each has its own set. It is meant to stop the *common*
    same-process retry/double-call, not to be a distributed exactly-once system.
    For stronger guarantees, back this with Redis or a DB unique key later.
"""

from __future__ import annotations

import time

#: How long a send fingerprint blocks an identical resend, in seconds.
_TTL_SECONDS = 90.0

#: fingerprint -> expiry monotonic timestamp.
_seen: dict[str, float] = {}


def _purge(now: float) -> None:
    """Drop expired fingerprints so the set can't grow unbounded."""
    for key, expiry in list(_seen.items()):
        if expiry < now:
            _seen.pop(key, None)


def already_sent(fingerprint: str) -> bool:
    """Return whether an identical send happened within the TTL window."""
    now = time.monotonic()
    _purge(now)
    return fingerprint in _seen


def mark_sent(fingerprint: str) -> None:
    """Record a successful send so an identical one is suppressed for the TTL."""
    _seen[fingerprint] = time.monotonic() + _TTL_SECONDS
