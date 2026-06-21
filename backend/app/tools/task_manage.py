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

_NOT_FOUND = "no matching item found"


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
        task = await repo.find_task(
            ctx.session, query=kwargs["task"], user_id=ctx.user_id
        )
        if task is None:
            return {"ok": False, "message": _NOT_FOUND}
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
                "description": "New reminder time as ISO-8601 date/time.",
            },
        },
        "required": ["task"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        task = await repo.find_task(
            ctx.session, query=kwargs["task"], user_id=ctx.user_id
        )
        if task is None:
            return {"ok": False, "message": _NOT_FOUND}
        updated = await repo.update_task(
            ctx.session,
            task_id=task.id,
            title=kwargs.get("new_title"),
            due_date=kwargs.get("due_date"),
        )
        assert updated is not None
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
        task = await repo.find_task(
            ctx.session, query=kwargs["task"], user_id=ctx.user_id
        )
        if task is None:
            return {"ok": False, "message": _NOT_FOUND}
        task_id, title = task.id, task.title
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
        note = await repo.find_note(
            ctx.session, query=kwargs["text"], user_id=ctx.user_id
        )
        if note is None:
            return {"ok": False, "message": _NOT_FOUND}
        note_id = note.id
        await repo.delete_note(ctx.session, note_id=note_id)
        return {"ok": True, "id": note_id, "deleted": True}
