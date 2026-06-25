"""``create_task`` tool: persist a to-do task with an optional due date."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import repo
from app.tools.base import Tool, ToolContext
from app.tools.validators import clean_text, validate_iso_datetime  # UX Spec §3.1


def resolve_due_date(
    due_date: str | None, remind_in_seconds: Any | None
) -> str | None:
    """Pick the absolute due date for a reminder.

    ``remind_in_seconds`` is preferred for *relative* times ("in 2 minutes")
    because the backend resolves it against the real current instant — the
    model never does fragile clock math, and a stale client clock can't push
    the reminder into the past. Falls back to an explicit absolute
    ``due_date`` (used for calendar times like "tomorrow at 5pm").
    """
    if remind_in_seconds is not None:
        try:
            secs = int(remind_in_seconds)
        except (TypeError, ValueError):
            secs = 0
        if secs > 0:
            when = datetime.now(timezone.utc) + timedelta(seconds=secs)
            # Whole seconds, explicit UTC offset — unambiguous for the phone.
            return when.replace(microsecond=0).isoformat()
    return due_date


class CreateTaskTool(Tool):
    """Create a to-do task with an optional ISO-8601 due date."""

    name = "create_task"
    description = "Create a to-do task with an optional due date."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "due_date": {
                "type": "string",
                "description": (
                    "Absolute ISO-8601 date/time for calendar reminders "
                    "(e.g. tomorrow at 5pm)."
                ),
            },
            "remind_in_seconds": {
                "type": "integer",
                "description": (
                    "Number of seconds from now for RELATIVE reminders "
                    "(e.g. 'in 2 minutes' -> 120, 'in 1 hour' -> 3600). "
                    "Prefer this for any 'in N minutes/hours/seconds' request."
                ),
            },
        },
        "required": ["title"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Persist the task and return its id, title, and due date."""
        # CHANGED (UX Spec §2.6 / §3.1): reject an empty title and validate an
        # absolute due_date as real ISO-8601 BEFORE persisting. Previously a
        # blank title or a junk date string ("next tuesday") was stored and
        # shipped to the phone's alarm clock. A valid date is preserved verbatim
        # (we store the original string, not a reformatted one).
        ok_title, title = clean_text(kwargs.get("title"), field="title", max_len=512)
        if not ok_title:
            return {"ok": False, "message": "What should the task be?"}
        ok_date, due_in = validate_iso_datetime(kwargs.get("due_date"))
        if not ok_date:
            return {
                "ok": False,
                "message": (
                    "That reminder time wasn't a clear date — when should I "
                    "remind you?"
                ),
            }
        due_date = resolve_due_date(due_in, kwargs.get("remind_in_seconds"))
        task = await repo.add_task(
            ctx.session,
            title=title,
            due_date=due_date,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
        )
        return {"id": task.id, "title": task.title, "due_date": task.due_date}
