"""``send_message`` tool: queue an outbound message to a known contact.

Delivery is stubbed — the message is persisted with status ``queued``. A real
deployment would hand off to an SMS/chat provider here and update the status.
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.tools.base import Tool, ToolContext


class SendMessageTool(Tool):
    """Send a text message to a known contact (stubbed delivery)."""

    name = "send_message"
    description = "Send a text message to a known contact."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "contact": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["contact", "text"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Persist the outbound message and return its id and status."""
        contact: str = kwargs["contact"]
        text: str = kwargs["text"]
        message = await repo.add_outbound_message(
            ctx.session,
            contact=contact,
            text=text,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
        )
        return {
            "id": message.id,
            "contact": message.contact,
            "status": message.status,
        }
