"""Voice-driven task/note management: complete, edit, and delete by name.

These let the user manage their list entirely by voice — "mark the milk task
done", "change the dentist task to Friday", "delete that note" — without ever
touching the app. Each tool finds the item by a fuzzy title/text match so the
model can act on what the user *said*, not an id.
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.tools.base import Tool, ToolContext
from app.tools.tasks import resolve_due_date
from app.tools.validators import validate_iso_datetime  # UX Spec §3.1

_NOT_FOUND = "no matching item found"


# CHANGED (UX Spec §3.5): shared helpers so every manage tool resolves a fuzzy
# name the SAME safe way — exactly one match acts; several matches ask the user
# which (never mutate the wrong item); none is a clean not-found.
def _ambiguous(kind: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an ``ambiguous`` result the model reads back as a question."""
    return {
        "ok": False,
        "status": "ambiguous",
        "message": (
            f"I found {len(options)} {kind}s matching that — which one did you "
            "mean?"
        ),
        "options": options,
    }


class CompleteTaskTool(Tool):
    """Mark a task as done by name."""

    name = "complete_task"
    description = (
        "Mark a to-do task as completed. Identify it by what the user said "
        "(e.g. 'the milk task'). Use when the user says a task is done."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Words from the task's title to find it.",
            }
        },
        "required": ["task"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        matches = await repo.find_tasks(
            ctx.session, query=kwargs["task"], user_id=ctx.user_id
        )
        if not matches:
            return {"ok": False, "message": _NOT_FOUND}
        if len(matches) > 1:
            return _ambiguous(
                "task", [{"id": t.id, "title": t.title} for t in matches]
            )
        task = matches[0]
        await repo.set_task_done(ctx.session, task_id=task.id, done=True)
        return {"ok": True, "id": task.id, "title": task.title, "done": True}


class UpdateTaskTool(Tool):
    """Edit a task's title and/or due date by name."""

    name = "update_task"
    description = (
        "Edit an existing task — change its title and/or its reminder time. "
        "Find it by what the user said. Provide the new title and/or a new "
        "ISO-8601 due date."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Words from the current title to find it.",
            },
            "new_title": {"type": "string"},
            "due_date": {
                "type": "string",
                "description": (
                    "New absolute reminder time as ISO-8601 date/time."
                ),
            },
            "remind_in_seconds": {
                "type": "integer",
                "description": (
                    "New RELATIVE reminder time in seconds from now "
                    "(e.g. 'in 10 minutes' -> 600). Prefer this for relative "
                    "times."
                ),
            },
        },
        "required": ["task"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        matches = await repo.find_tasks(
            ctx.session, query=kwargs["task"], user_id=ctx.user_id
        )
        if not matches:
            return {"ok": False, "message": _NOT_FOUND}
        if len(matches) > 1:
            return _ambiguous(
                "task", [{"id": t.id, "title": t.title} for t in matches]
            )
        task = matches[0]
        # CHANGED (UX Spec §3.1): validate a new absolute due_date before saving.
        ok_date, due_in = validate_iso_datetime(kwargs.get("due_date"))
        if not ok_date:
            return {
                "ok": False,
                "message": "That new reminder time wasn't a clear date.",
            }
        updated = await repo.update_task(
            ctx.session,
            task_id=task.id,
            title=kwargs.get("new_title"),
            due_date=resolve_due_date(due_in, kwargs.get("remind_in_seconds")),
        )
        # CHANGED (UX Spec §3.3): replaced `assert updated is not None`. If the
        # row was deleted between find and update (a TOCTOU race), the assert
        # raised AssertionError → a raw stack string to the model. Now we return
        # a clean not-found instead.
        if updated is None:
            return {"ok": False, "message": _NOT_FOUND}
        return {
            "ok": True,
            "id": updated.id,
            "title": updated.title,
            "due_date": updated.due_date,
        }


class DeleteTaskTool(Tool):
    """Delete a task by name."""

    name = "delete_task"
    description = "Delete a to-do task, found by what the user said."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"task": {"type": "string"}},
        "required": ["task"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        matches = await repo.find_tasks(
            ctx.session, query=kwargs["task"], user_id=ctx.user_id
        )
        if not matches:
            return {"ok": False, "message": _NOT_FOUND}
        if len(matches) > 1:
            # Deleting the wrong task is bad — make the model confirm which.
            return _ambiguous(
                "task", [{"id": t.id, "title": t.title} for t in matches]
            )
        task_id, title = matches[0].id, matches[0].title
        await repo.delete_task(ctx.session, task_id=task_id)
        return {"ok": True, "id": task_id, "title": title, "deleted": True}


class DeleteNoteTool(Tool):
    """Delete a note by its text."""

    name = "delete_note"
    description = "Delete a saved note, found by words from its text."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        matches = await repo.find_notes(
            ctx.session, query=kwargs["text"], user_id=ctx.user_id
        )
        if not matches:
            return {"ok": False, "message": _NOT_FOUND}
        if len(matches) > 1:
            # Deleting the wrong note is bad — make the model confirm which.
            return _ambiguous(
                "note",
                [{"id": n.id, "text": n.text[:60]} for n in matches],
            )
        note_id = matches[0].id
        await repo.delete_note(ctx.session, note_id=note_id)
        return {"ok": True, "id": note_id, "deleted": True}
