"""Which emails matter — a deterministic first pass the model refines.

``read_emails`` / ``inbox_summary`` hand every message through
:func:`score_importance` so the assistant can answer "anything important?"
from evidence instead of a hunch. The score is built only from what the
mailbox itself says (Gmail's Important label, a star, the unread flag, the
headers) plus a small vocabulary of words that mark urgency in the languages
FarryOn's users write in. It is deliberately conservative: a newsletter that
shouts "URGENT: sale ends tonight" is pulled back down by its bulk-mail
headers, so "critical" is reserved for mail a person would actually want to
be interrupted for.

Pure functions, no I/O — safe to unit-test exhaustively.
"""

from __future__ import annotations

import re
from typing import Any

#: Words that, in a subject line, mean "drop what you're doing" — English plus
#: the Hinglish/Urdu the app's users speak. Whole-word, case-insensitive.
CRITICAL_WORDS: tuple[str, ...] = (
    "urgent", "urgently", "asap", "emergency", "immediately", "critical",
    "final notice", "last notice", "overdue", "past due", "suspended",
    "suspension", "security alert", "suspicious", "unauthorized",
    "unauthorised", "action required", "action needed", "response required",
    "deadline today", "expires today", "expiring today", "last reminder",
    "turant", "zaroori", "zaruri", "jaldi", "fauran", "foran", "abhi",
)

#: Words that make a message worth mentioning, without being a fire alarm.
HIGH_WORDS: tuple[str, ...] = (
    "deadline", "due", "invoice", "payment", "paid", "receipt", "reminder",
    "interview", "offer", "contract", "agreement", "appointment", "confirm",
    "confirmation", "approval", "approve", "please respond", "please reply",
    "rsvp", "meeting", "otp", "verification code", "password", "bank",
    "salary", "tax", "legal", "court", "visa", "ticket", "booking", "flight",
    "important", "attention", "follow up", "follow-up", "escalation",
    "escalated", "complaint", "refund", "delivery", "delivered", "failed",
    "error", "declined", "rejected", "accepted", "approved",
)

#: Header names whose presence marks bulk / list mail.
_BULK_HEADERS = ("list-unsubscribe", "list-id", "list-post")

_NOREPLY_RE = re.compile(r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon)", re.I)


def _word_re(words: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![\w])(?:{alts})(?![\w])", re.I)


_CRITICAL_RE = _word_re(CRITICAL_WORDS)
_HIGH_RE = _word_re(HIGH_WORDS)

#: Score thresholds → level.
_CRITICAL_AT = 6
_HIGH_AT = 3


def _hits(rx: re.Pattern[str], text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in rx.finditer(text or "")})


def score_importance(
    item: dict[str, Any], user_address: str | None = None
) -> tuple[str, list[str], int]:
    """``(level, reasons, score)`` for one fetched message.

    ``item`` is the dict :mod:`app.tools.email_read` builds for a message. The
    keys it reads (all optional): ``subject``, ``snippet``, ``from_email``,
    ``to``, ``cc``, ``unread``, ``flagged``, ``gmail_important``,
    ``bulk`` (List-* headers present), ``priority`` (``high``/``normal``),
    ``in_reply_to`` (non-empty when the mail is part of a conversation).

    ``level`` is one of ``critical`` / ``high`` / ``normal`` / ``low``.
    ``reasons`` are short, speakable phrases ("Gmail marked it important",
    "mentions 'deadline'") so the assistant can say WHY, not just THAT.
    """
    score = 0
    reasons: list[str] = []
    subject = item.get("subject") or ""
    snippet = item.get("snippet") or ""
    from_email = (item.get("from_email") or "").lower()

    # --- what the mailbox itself says ---------------------------------------
    if item.get("gmail_important"):
        score += 3
        reasons.append("Gmail marked it important")
    if item.get("flagged"):
        score += 3
        reasons.append("it's starred")
    if (item.get("priority") or "").lower() == "high":
        score += 2
        reasons.append("sent as high priority")
    if item.get("unread"):
        score += 1

    # --- words that signal urgency ------------------------------------------
    crit_subject = _hits(_CRITICAL_RE, subject)
    crit_body = [w for w in _hits(_CRITICAL_RE, snippet) if w not in crit_subject]
    if crit_subject:
        score += 4
        reasons.append(f"the subject says '{crit_subject[0]}'")
    elif crit_body:
        score += 2
        reasons.append(f"it mentions '{crit_body[0]}'")

    high_subject = _hits(_HIGH_RE, subject)
    high_body = [w for w in _hits(_HIGH_RE, snippet) if w not in high_subject]
    if high_subject:
        score += 2
        reasons.append(f"it's about '{high_subject[0]}'")
    elif high_body:
        score += 1

    # --- who it is from / to -------------------------------------------------
    recipients = [
        a.lower() for a in (item.get("to") or []) + (item.get("cc") or [])
    ]
    if user_address and user_address.lower() in [
        a.lower() for a in (item.get("to") or [])
    ] and len(recipients) <= 3:
        score += 1
        reasons.append("sent directly to you")
    if item.get("in_reply_to") or re.match(r"^\s*re\s*:", subject, re.I):
        score += 1
        reasons.append("part of a conversation you're in")

    # --- bulk mail pulls it back down ---------------------------------------
    if item.get("bulk"):
        score -= 3
        reasons.append("it's a newsletter or bulk mail")
    if _NOREPLY_RE.search(from_email):
        score -= 1

    if score >= _CRITICAL_AT:
        level = "critical"
    elif score >= _HIGH_AT:
        level = "high"
    elif score < 0:
        level = "low"
    else:
        level = "normal"
    return level, reasons, score


_LEVEL_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}


def rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Most important first; within a level, highest score then newest first.

    Reads the ``importance`` / ``_score`` / ``date`` keys that
    :mod:`app.tools.email_read` attaches to every fetched item.
    """
    return sorted(
        items,
        key=lambda e: (
            _LEVEL_ORDER.get(e.get("importance") or "normal", 2),
            -(e.get("_score") or 0),
            # ISO dates sort lexically; negate via reverse-friendly trick.
            _neg_date(e.get("date") or ""),
        ),
    )


def _neg_date(iso: str) -> tuple[int, ...]:
    """A key that orders ISO timestamps newest-first inside an ascending sort."""
    digits = [int(c) for c in iso if c.isdigit()][:14]  # YYYYMMDDHHMMSS
    digits += [0] * (14 - len(digits))
    return tuple(-d for d in digits)
