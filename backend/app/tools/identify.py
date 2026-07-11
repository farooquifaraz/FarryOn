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
from app.logging_conf import get_logger
from app.services.vision import run_detection
from app.tools.base import Tool, ToolContext

logger = get_logger(__name__)

class IdentifyImageTool(Tool):
    """Identify the landmark or product currently in the camera view."""

    name = "identify_image"
    description = (
        "Capture the current camera view and identify what it shows — a "
        "landmark/place, a product, or any ordinary object. Use whenever the "
        "user wants to know what they are looking at: 'what is this', 'what's "
        "in front of me', 'take a photo and tell me what it is', 'click a pic', "
        "'scan this', 'identify/describe this'. No tap is needed.\n"
        "IMPORTANT: if the user asks to READ or ANSWER something about the view "
        "— the TIME on a clock, text on a label/sign, a number, how many of "
        "something, or any specific question — pass that as 'question' (e.g. "
        "question='what time does the clock show?'). That reads the image to "
        "answer, instead of trying to identify it as a product to shop for. "
        "Use 'kind' only for pure what-is-this: 'landmark', 'product', or "
        "'auto' (default)."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["landmark", "product", "auto"],
                "description": "What to identify; default 'auto'.",
            },
            "question": {
                "type": "string",
                "description": "A specific question to READ/answer from the view "
                "(time on a clock, text on a label, a count, etc.). When set, "
                "the image is read to answer this instead of product/landmark "
                "identification.",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Run detection on the cached camera frame and return the result."""
        # Only answer on a frame captured AFTER this request began. Otherwise a
        # ≤10s-old frame from a PREVIOUS question (user has since looked
        # elsewhere) passes a plain staleness check and we describe the wrong,
        # stale scene. The client snaps a fresh photo when it sees this tool
        # call; we wait for THAT frame (arrival time must be >= t0).
        t0 = time.monotonic()

        def _fresh() -> bool:
            return (
                ctx.last_frame is not None
                and ctx.last_frame_at is not None
                and ctx.last_frame_at >= t0
            )

        # Wait (once) for the just-triggered capture to land. Phone-camera
        # frames stream ~1 fps so this returns almost immediately; the glasses
        # photo takes ~4-5 s.
        if not _fresh() and ctx.wait_for_frame is not None:
            await ctx.wait_for_frame(timeout=8.0)

        if not _fresh():
            return {
                "ok": False,
                "error": (
                    "I couldn't get a fresh look just now. Make sure the camera "
                    "is on and pointed at it, then ask again."
                ),
            }

        kind = kwargs.get("kind") or "auto"
        if kind not in ("landmark", "product", "auto"):
            kind = "auto"
        question = (kwargs.get("question") or "").strip() or None

        image_data = base64.b64encode(ctx.last_frame).decode("utf-8")
        # CHANGED (UX Spec §3.3): wrap the vision call so a Vision API outage,
        # bad credentials, or quota error becomes a friendly {ok:false,error}
        # the model can speak — instead of a raw "GoogleAPIError: ..." stack
        # string reaching the model via the engine's generic handler. The
        # vision service already returns its own {ok,...} envelope on expected
        # failures; this catch is the last-resort net for the unexpected.
        try:
            return await run_detection(
                kind,  # type: ignore[arg-type]
                settings=get_settings(),
                image_data=image_data,
                question=question,
            )
        except Exception as exc:  # noqa: BLE001 - never surface a raw stack
            logger.error("identify_image.detection_error", error=repr(exc))
            return {
                "ok": False,
                "error": (
                    "I couldn't scan that just now — point the camera at the "
                    "subject and try once more."
                ),
            }
