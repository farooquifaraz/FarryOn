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
    """Fields the confirmed call gave that differ from the approved draft."""
    return [
        k for k, v in new.items()
        if v not in (None, "", []) and _norm(v) != _norm(old.get(k))
    ]


def _current_turn(ctx: ToolContext) -> int:
    return ctx.user_turn() if ctx.user_turn is not None else 0


def _preview(kind: str, fields: dict[str, Any], account: str,
             note: str | None = None) -> dict[str, Any]:
    """The tool result that asks the model to read the draft back."""
    shown = {k: v for k, v in fields.items() if v not in (None, "", [])}
    what = "email" if kind == "send" else "forward"
    instruction = (
        f"NOT SENT YET. Read this {what} back to the user — the recipient "
        "address exactly as written (spell out anything unusual), any cc, "
        "the subject and the full text — and ask if you should send it. If "
        "the user never gave this address in full, ask for it instead of "
        "reading it back. Only after they say yes, call the tool again with "
        "confirmed=true."
    )
    if note:
        instruction = note + " " + instruction
    return {
        "ok": True,
        "sent": False,
        "status": "confirm_needed",
        "from": account,
        "draft": shown,
        "_instruction": instruction,
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
    return Gate({**draft["fields"]})


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
