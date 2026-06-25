"""``create_note`` tool: persist a short note for the user."""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.tools.base import Tool, ToolContext
from app.tools.validators import clean_text  # UX Spec §3.1


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
        # CHANGED (UX Spec §2.5 / §3.1): reject an empty note and cap its length
        # so a blank or multi-megabyte payload can't be persisted.
        ok_text, text = clean_text(kwargs.get("text"), field="note", max_len=2000)
        if not ok_text:
            return {"ok": False, "message": "What should the note say?"}
        note = await repo.add_note(
            ctx.session,
            text=text,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
        )
        return {"id": note.id, "text": note.text}
