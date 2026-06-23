"""``identify_image`` tool: identify the landmark or product the camera sees.

When the user points the camera at something and asks "what landmark is this?"
or "what is this product?", the model calls this tool. It reads the latest camera
frame cached on the session (``ctx.last_frame``), runs Google Cloud Vision via
:mod:`app.services.vision`, and returns structured info (name, GPS/Maps,
Wikipedia, or marketplace links) which the model speaks back and the app renders.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from app.config import get_settings
from app.services.vision import run_detection
from app.tools.base import Tool, ToolContext

#: Frames older than this are treated as no-frame — the camera was likely
#: lowered/turned off, so identifying the last frame would answer about a stale
#: scene. The device streams ~1 fps while the camera is on.
_FRAME_STALE_SECONDS = 10.0


class IdentifyImageTool(Tool):
    """Identify the landmark or product currently in the camera view."""

    name = "identify_image"
    description = (
        "Capture the current camera view and identify what it shows — a "
        "landmark/place, a product, or any ordinary object. Use whenever the "
        "user wants to know what they are looking at: 'what is this', 'what's "
        "in front of me', 'take a photo and tell me what it is', 'click a pic', "
        "'scan this', 'identify/describe this'. No tap is needed. 'kind' picks "
        "the type: 'landmark', 'product', or 'auto' (default — auto-detects "
        "landmark vs product vs object). Prefer 'auto' unless clearly told."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["landmark", "product", "auto"],
                "description": "What to identify; default 'auto'.",
            }
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Run detection on the cached camera frame and return the result."""
        stale = (
            ctx.last_frame_at is None
            or (time.monotonic() - ctx.last_frame_at) > _FRAME_STALE_SECONDS
        )
        if not ctx.last_frame or stale:
            return {
                "ok": False,
                "error": (
                    "I can't see a current camera frame. Make sure the camera is "
                    "on and pointed at the subject, then ask again."
                ),
            }

        kind = kwargs.get("kind") or "auto"
        if kind not in ("landmark", "product", "auto"):
            kind = "auto"

        image_data = base64.b64encode(ctx.last_frame).decode("utf-8")
        return await run_detection(
            kind,  # type: ignore[arg-type]
            settings=get_settings(),
            image_data=image_data,
        )
