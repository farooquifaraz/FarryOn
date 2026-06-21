"""Read-back tools: let the assistant recall the user's notes and tasks.

These close the agentic loop — the model can not only *create* notes/tasks but
look them up and read them back ("what are my notes?", "what's on my to-do
list?"). They return compact, speakable summaries.
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.tools.base import Tool, ToolContext


class ListNotesTool(Tool):
    """Retrieve the user's saved notes."""

    name = "list_notes"
    description = (
        "Retrieve the user's saved notes (most recent first) so you can read "
        "them back. Use when the user asks what notes they have, or to find a "
        "note they mention."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max notes to return (default 10).",
            }
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        limit = int(kwargs.get("limit") or 10)
        limit = max(1, min(50, limit))
        notes = await repo.list_notes(
            ctx.session, user_id=ctx.user_id, limit=limit
        )
        return {
            "count": len(notes),
            "notes": [
                {"id": n.id, "text": n.text} for n in notes
            ],
        }


class ListTasksTool(Tool):
    """Retrieve the user's to-do tasks."""

    name = "list_tasks"
    description = (
        "Retrieve the user's to-do tasks (open ones first). Use when the user "
        "asks what tasks or reminders they have, or what's due."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "include_done": {
                "type": "boolean",
                "description": "Include completed tasks (default false).",
            },
            "limit": {
                "type": "integer",
                "description": "Max tasks to return (default 10).",
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        include_done = bool(kwargs.get("include_done") or False)
        limit = int(kwargs.get("limit") or 10)
        limit = max(1, min(50, limit))
        tasks = await repo.list_tasks(
            ctx.session,
            user_id=ctx.user_id,
            include_done=include_done,
            limit=limit,
        )
        return {
            "count": len(tasks),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "due_date": t.due_date,
                    "done": t.done,
                }
                for t in tasks
            ],
        }
