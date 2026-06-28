"""Shared input validators for tools (FarryOn Product UX Spec §3.1).

The :class:`~app.agent.tool_engine.ToolEngine` validator only checks JSON *type*
and *required* presence — it does NOT check empty strings, length, ranges, or
formats (email / phone / ISO date). Those value-level guards belong here so every
tool validates consistently and returns a friendly ``{ok: false, message}``
instead of letting bad input reach the DB, an SMTP server, or a deep link.

All helpers are pure and side-effect free. They never raise for *expected* bad
input — they return a ``(ok, value)`` signal so the calling tool can produce a
friendly message. Import only what you need:

    from app.tools.validators import clean_text, valid_email, valid_phone, validate_iso_datetime
"""

from __future__ import annotations

import re
from datetime import datetime

# A pragmatic, non-pedantic email check. We deliberately do NOT try to fully
# implement RFC 5322 (that regex is famously monstrous); this rejects the obvious
# bad cases the old ``"@" in to`` check let through: ``a``, ``@b``, ``a@``,
# ``a@b`` (no TLD), and addresses with spaces.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

# E.164 allows 1–3 digit country codes + national number, max 15 digits total.
# A real number is realistically >= 7 digits, so anything shorter is junk
# (e.g. a single mis-heard digit) and must be rejected.
_PHONE_MIN_DIGITS = 7
_PHONE_MAX_DIGITS = 15


def clean_text(
    value: object, *, field: str = "text", max_len: int = 2000
) -> tuple[bool, str]:
    """Trim, reject empty, and cap length.

    Returns ``(ok, cleaned)``. ``ok`` is ``False`` when the input is missing or
    blank after stripping. ``cleaned`` is always trimmed and length-capped so a
    multi-megabyte model/voice payload can never bloat the DB or a deep-link URL.

    Args:
        value: The raw argument (any type; non-str becomes empty).
        field: Name used by the caller in its friendly message.
        max_len: Hard cap; text longer than this is truncated, not rejected.
    """
    s = value.strip() if isinstance(value, str) else ""
    if not s:
        return False, ""
    if len(s) > max_len:
        s = s[:max_len]
    return True, s


def valid_email(value: object) -> tuple[bool, str]:
    """Validate an email address. Returns ``(ok, trimmed_address)``.

    Replaces the previous weak ``"@" in to`` check, which accepted ``@``,
    ``a@`` and ``a@b`` (no TLD).
    """
    s = value.strip() if isinstance(value, str) else ""
    return bool(_EMAIL_RE.match(s)), s


def normalize_phone(phone: object, default_cc: str) -> str:
    """Digits-only number with a country code (no ``+``); ``""`` if no digits.

    Kept identical in behaviour to the original ``whatsapp.normalize_phone`` so
    existing callers/tests are unaffected — :func:`valid_phone` adds the new
    length sanity check on top of this.
    """
    digits = re.sub(r"\D", "", phone if isinstance(phone, str) else "")
    if not digits:
        return ""
    if digits.startswith(default_cc):
        return digits
    return default_cc + digits.lstrip("0")


def valid_phone(phone: object, default_cc: str) -> tuple[bool, str]:
    """Normalize AND sanity-check a phone number's digit count.

    Returns ``(ok, normalized_digits)``. ``ok`` is ``False`` when the number is
    empty or has an implausible length (< 7 or > 15 digits) — which is how a
    single mis-heard digit used to slip through into a broken ``wa.me`` link.
    The wa.me / sms: deep links require plain international digits, no ``+``,
    which is exactly what ``normalized_digits`` is.
    """
    clean = normalize_phone(phone, default_cc)
    ok = _PHONE_MIN_DIGITS <= len(clean) <= _PHONE_MAX_DIGITS
    return ok, clean


def validate_iso_datetime(value: object) -> tuple[bool, str | None]:
    """Validate an ABSOLUTE ISO-8601 date-time WITHOUT reformatting it.

    Returns ``(ok, original_or_None)``:
      * empty/absent  -> ``(True, None)``  (a due date is optional)
      * valid ISO     -> ``(True, original_string)``  (preserved verbatim so a
        reminder time the model produced is stored exactly as given)
      * unparseable   -> ``(False, None)`` (e.g. "next tuesday" — the caller
        should ask again instead of shipping junk to the phone's alarm clock)

    A trailing ``Z`` (UTC) is accepted for the parse probe even on Python
    versions where ``fromisoformat`` is strict about it; the original string is
    still what we return.
    """
    s = value.strip() if isinstance(value, str) else ""
    if not s:
        return True, None
    probe = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        datetime.fromisoformat(probe)
    except (TypeError, ValueError):
        return False, None
    return True, s


# --- Sensitive-content detection (so a mis-heard OTP/password/card isn't sent
#     to the wrong person without an explicit extra confirmation) -------------

_SENSITIVE_KEYWORDS: dict[str, re.Pattern[str]] = {
    "an OTP / login code": re.compile(
        r"\b(otp|one[\s-]?time\s*(pass(word|code))?|verification code|"
        r"auth(entication)? code|login code|security code|code is|your code)\b",
        re.I,
    ),
    "a password / PIN": re.compile(
        r"\b(pass\s?word|pass\s?code|\bpwd\b|\bpin\b|p\.i\.n)\b", re.I
    ),
    "a CVV": re.compile(r"\b(cvv|cvc|card verification)\b", re.I),
    "bank account details": re.compile(
        r"\b(account\s*(number|no\.?|#)|iban|swift code|routing number|"
        r"sort code)\b",
        re.I,
    ),
}

_CARD_RE = re.compile(r"(?:\d[ -]?){13,19}")


def _luhn_ok(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def scan_sensitive(text: object) -> list[str]:
    """Return the kinds of sensitive data a message appears to contain.

    Keyword cues (OTP, password, PIN, CVV, account/IBAN) plus a real
    credit-card check (13-19 digits passing the Luhn checksum). Empty list means
    nothing suspicious. Intentionally conservative — it only flags clear cues,
    so it warns on real secrets without nagging on ordinary chat.
    """
    s = text if isinstance(text, str) else ""
    if not s:
        return []
    found: list[str] = []
    for label, rx in _SENSITIVE_KEYWORDS.items():
        if rx.search(s):
            found.append(label)
    for m in _CARD_RE.finditer(s):
        if _luhn_ok(m.group()):
            found.append("a card number")
            break
    return sorted(set(found))
