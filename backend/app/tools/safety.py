"""Shared pre-send safety gates for the messaging tools.

Every send tool (WhatsApp / SMS / Telegram) runs the same two checks before it
acts, so the behaviour is identical across channels:

  * :func:`sensitive_gate` — if the message looks like it carries an OTP,
    password, card number, etc., block the send and ask the model to get an
    explicit extra confirmation (resend with ``confirm_sensitive=True``).
  * :func:`rate_gate` — block a burst of sends from the same session.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from app.tools import ratelimit
from app.tools.validators import scan_sensitive

#: Per-session set of message fingerprints we've already flagged as sensitive.
#: ``confirm_sensitive`` is only honoured for a message that was ACTUALLY
#: blocked here first — so the model can't skip the warning by setting the flag
#: preemptively. The gate is enforced server-side, not on the model's word.
_flagged: dict[str, set[str]] = defaultdict(set)


def sensitive_gate(
    message: str, confirm_sensitive: bool, session_id: str | None = None
) -> dict[str, Any] | None:
    """Return a blocking result if ``message`` is sensitive and not yet
    confirmed THROUGH this gate (a prior block for the same message)."""
    kinds = scan_sensitive(message)
    if not kinds:
        return None
    key = session_id or "_anon"
    fp = hashlib.sha1(message.encode("utf-8")).hexdigest()
    if confirm_sensitive and fp in _flagged[key]:
        return None  # we blocked it before AND the user has now confirmed
    _flagged[key].add(fp)  # remember we flagged this exact message
    return {
        "ok": False,
        "status": "sensitive_confirm_needed",
        "sensitive_kinds": kinds,
        "message": (
            "This message looks like it contains " + ", ".join(kinds) + ". "
            "Warn the user it's sensitive, read it back, and only resend after "
            "they explicitly say yes."
        ),
    }


def rate_gate(session_id: str | None) -> dict[str, Any] | None:
    """Return a blocking result if this session is sending too fast."""
    if ratelimit.allow(session_id):
        return None
    return {
        "ok": False,
        "status": "rate_limited",
        "message": (
            "That's several messages in a short time — let's pause a moment "
            "before sending any more."
        ),
    }
