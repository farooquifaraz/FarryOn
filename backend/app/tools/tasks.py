"""``create_task`` tool: persist a to-do task with an optional due date."""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.tools.base import Tool, ToolContext


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
                "description": "ISO-8601 date/time",
            },
        },
        "required": ["title"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Persist the task and return its id, title, and due date."""
        title: str = kwargs["title"]
        due_date: str | None = kwargs.get("due_date")
        task = await repo.add_task(
            ctx.session,
            title=title,
            due_date=due_date,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
        )
        return {"id": task.id, "title": task.title, "due_date": task.due_date}
