"""Draft → the user's answer → send, enforced by the server.

The prompt already told the model to read a mail back and wait for "yes".
On device (2026-09-12, E7.4) it did neither: the user said "ali at gmail",
the model wrote ``ali@gmail.com`` itself and sent in the same breath. A rule
the model can skip is not a safety rule, so the send tools enforce it:

1. A call without ``confirmed=true`` never sends. It stores the draft and
   answers with exactly what to read back.
2. A call with ``confirmed=true`` sends only a draft that was shown earlier
   in this session AND after which the user has spoken (a later user turn).
   The same breath cannot both show and send.
3. The confirmed call may leave the body (or subject, recipients) out — the
   approved draft fills them in (E7.1: the body was dropped after the yes and
   the send failed). If it CHANGES the recipients, subject or body, that is a
   new draft and goes back to step 1: what goes out is what the user heard.
   (A subject the draft did not have may be added.)
4. The turn that answers must not be a refusal: words like no / wait /
   cancel / nahi / ruko, with no yes among them, keep the draft unsent. The
   answer is read from the live transcript of that turn — its final text only
   arrives after the model has already acted.

Drafts live per session (the orchestrator's shared dict) and expire after
ten minutes; a reconnect starts clean, so a stale "yes" can never send.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.tools.base import ToolContext

#: How long an unanswered draft stays sendable.
DRAFT_TTL_S = 600.0

#: The body echoed back in a preview. The tool result is clipped for the
#: model at 6000 chars from the END; a longer echo would cut the guidance.
_PREVIEW_BODY_CHARS = 2500

#: Fields a confirmed call may fill in although the draft had them empty —
#: never a recipient (that would add someone the user did not hear).
_FILLABLE = frozenset({"subject"})

_YES = frozenset((
    "yes yeah yep yup ya sure ok okay k send sent go correct right perfect fine "
    "good great confirm confirmed please haan han haa ha hanji ji jee theek thik "
    "bilkul zaroor zarur bhejo bhej bhejdo kardo karo chalo done "
    "हाँ हां हा जी ठीक बिल्कुल ज़रूर जरूर भेजो भेज भेजदो करो कर चलो "
    "نعم أرسل ہاں جی ٹھیک بھیجو بھیج"
).split())
_NO = frozenset((
    "no nope nah dont don't not cancel stop wait hold nahi nahin nai mat ruko "
    "ruk rukiye rehne band "
    "नहीं नही ना मत रुको रुक रुकिए रहने "
    "لا نہیں نہ مت رکو رک"
).split())


@dataclass(slots=True)
class Gate:
    """Outcome of :func:`gate`: send ``fields``, or answer ``response``."""

    fields: dict[str, Any]
    response: dict[str, Any] | None = None


def is_confirmed(value: Any) -> bool:
    """``confirmed`` as the model sends it: a bool, or the string "true"."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "yes", "1")


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, list):
        return sorted(_norm(v) for v in value)
    return value


def _changed(new: dict[str, Any], old: dict[str, Any]) -> list[str]:
    """Fields the confirmed call gave that differ from the approved draft
    (filling an empty subject is not a change)."""
    return [
        k for k, v in new.items()
        if v not in (None, "", [])
        and _norm(v) != _norm(old.get(k))
        and not (k in _FILLABLE and old.get(k) in (None, ""))
    ]


def declines(text: str) -> bool:
    """Whether the user's words refuse: a no/wait/cancel with no yes."""
    words = set(
        "".join(c if c.isalnum() or c in "'’" else " " for c in text.casefold())
        .replace("’", "'").split()
    )
    return bool(words & _NO) and not (words & _YES)


def _current_turn(ctx: ToolContext) -> int:
    return ctx.user_turn() if ctx.user_turn is not None else 0


def _preview(kind: str, fields: dict[str, Any], account: str,
             note: str | None = None) -> dict[str, Any]:
    """The tool result that asks the model to read the draft back."""
    shown = {k: v for k, v in fields.items() if v not in (None, "", [])}
    body = shown.get("body")
    if isinstance(body, str) and len(body) > _PREVIEW_BODY_CHARS:
        shown["body"] = (
            body[:_PREVIEW_BODY_CHARS]
            + f" …[{len(body) - _PREVIEW_BODY_CHARS} more characters]"
        )
    what = "email" if kind == "send" else "forward"
    instruction = (
        f"NOT SENT YET. Read this {what} back to the user — the recipient "
        "address exactly as written (spell out anything unusual), any cc, "
        "the subject and the text (the gist, if it is long) — and ask if you "
        "should send it. If the user never gave this address in full, ask "
        "for it instead of reading it back. Only after they say yes, call "
        "the tool again with confirmed=true."
    )
    if kind == "send" and not shown.get("subject"):
        instruction += " It has no subject yet: suggest a short one."
    if note:
        instruction = note + " " + instruction
    # Guidance first, the draft last: a clipped result loses its tail.
    return {
        "ok": True,
        "sent": False,
        "status": "confirm_needed",
        "_instruction": instruction,
        "from": account,
        "draft": shown,
    }


def gate(ctx: ToolContext, kind: str, fields: dict[str, Any], *,
         confirmed: bool, account: str) -> Gate:
    """Decide whether this call may send. ``fields`` are the call's own
    arguments (recipients as lists); the returned ``fields`` are what to
    send — the call's, completed from the approved draft."""
    drafts = ctx.email_drafts
    if drafts is None:
        # Outside a live session there is no conversation to confirm in (the
        # orchestrator always supplies the dict — test_email_drafts pins it).
        return Gate(fields)

    now = time.monotonic()
    turn = _current_turn(ctx)
    draft = drafts.get(kind)
    if draft is not None and (
        now - draft["at"] > DRAFT_TTL_S or draft["account"] != account
    ):
        draft = None

    def new_draft(filled: dict[str, Any], note: str | None = None) -> Gate:
        drafts[kind] = {"fields": filled, "turn": turn, "at": now, "account": account}
        return Gate(filled, _preview(kind, filled, account, note))

    if not confirmed:
        return new_draft(fields)
    if draft is None:
        return new_draft(
            fields,
            "There is no draft the user has approved yet.",
        )
    changed = _changed(fields, draft["fields"])
    if changed:
        merged = {**draft["fields"], **{k: fields[k] for k in changed}}
        return new_draft(
            merged,
            "This differs from what the user approved ("
            + ", ".join(changed) + ").",
        )
    if turn <= draft["turn"]:
        # Shown and "confirmed" in the same breath: the user never answered.
        return Gate(
            draft["fields"],
            _preview(kind, draft["fields"], account,
                     "The user has not answered this draft yet."),
        )
    said = ctx.user_text() if ctx.user_text is not None else None
    if said is not None and declines(said):
        return Gate(
            draft["fields"],
            _preview(kind, draft["fields"], account,
                     f"The user did not agree (they said: «{said.strip()[:200]}»). "
                     "Do not send; ask what to change."),
        )
    filled = {**draft["fields"]}
    for k in _FILLABLE:
        if fields.get(k) and not filled.get(k):
            filled[k] = fields[k]
    return Gate(filled)


def pending(ctx: ToolContext, kind: str) -> dict[str, Any] | None:
    """The fields of the live draft of ``kind``, if any (read-only)."""
    draft = (ctx.email_drafts or {}).get(kind)
    if draft is None or time.monotonic() - draft["at"] > DRAFT_TTL_S:
        return None
    return dict(draft["fields"])


def sent(ctx: ToolContext, kind: str) -> None:
    """Forget the draft once it went out: a second "yes" sends nothing."""
    if ctx.email_drafts is not None:
        ctx.email_drafts.pop(kind, None)
