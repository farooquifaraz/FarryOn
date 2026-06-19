"""``create_note`` tool: persist a short note for the user."""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.tools.base import Tool, ToolContext


class CreateNoteTool(Tool):
    """Save a short note for the user."""

    name = "create_note"
    description = "Save a short note for the user."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Persist the note and return its id and text."""
        text: str = kwargs["text"]
        note = await repo.add_note(
            ctx.session,
            text=text,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
        )
        return {"id": note.id, "text": note.text}
