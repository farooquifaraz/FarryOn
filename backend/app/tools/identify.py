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
from app.tools.quota import check_quota
from app.tools.capture_feedback import capture_failure_message

logger = get_logger(__name__)


def _first_landmark_maps_url(envelope: dict[str, Any]) -> str | None:
    """Return the Google Maps link of the first landmark in a detection
    envelope, or ``None`` if there is no landmark with a location."""
    landmarks = (envelope.get("result") or {}).get("landmarks") or []
    for lm in landmarks:
        maps_url = lm.get("maps_url")
        if maps_url:
            return maps_url
    return None


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
        blocked = await check_quota(ctx, "image_scans")
        if blocked:
            return blocked
        # Only answer on a frame captured AFTER this request began. Otherwise a
        # ≤10s-old frame from a PREVIOUS question (user has since looked
        # elsewhere) passes a plain staleness check and we describe the wrong,
        # stale scene. The client snaps a fresh photo when it sees this tool
        # call; we wait for THAT frame (arrival time must be >= t0).
        t0 = time.monotonic()

        def _current() -> tuple[bytes | None, float | None]:
            # The LIVE frame, not the dispatch-time snapshot: the glasses
            # photo lands ~5 s into the wait below, and only the orchestrator's
            # live fields see it. Re-checking the snapshot here rejected a
            # perfectly delivered photo (device-proven 2026-07-11).
            if ctx.latest_frame is not None:
                return ctx.latest_frame()
            return (ctx.last_frame, ctx.last_frame_at)

        def _fresh() -> bool:
            frame, arrived_at = _current()
            return frame is not None and arrived_at is not None and arrived_at >= t0

        # Wait (once) for the just-triggered capture to land. Phone-camera
        # frames stream ~1 fps so this returns almost immediately; the glasses
        # photo takes ~4-5 s. The timeout is the session's device-appropriate
        # default (Settings.frame_wait_seconds / glasses_frame_wait_seconds);
        # a device-reported capture failure wakes the wait early with the
        # precise reason.
        if not _fresh() and ctx.wait_for_frame is not None:
            await ctx.wait_for_frame()

        if not _fresh():
            reason = ctx.capture_error() if ctx.capture_error is not None else None
            return {"ok": False, "error": capture_failure_message(reason)}

        kind = kwargs.get("kind") or "auto"
        if kind not in ("landmark", "product", "auto"):
            kind = "auto"
        question = (kwargs.get("question") or "").strip() or None

        frame, _ = _current()
        assert frame is not None  # guaranteed by the _fresh() gate above

        # A question about the view is answered by the model that is already
        # looking at it.
        #
        # The frame reached the live model on its way here — that is what the
        # wait above was for — so it has the picture in context. Sending it to
        # a SECOND vision model and waiting for that answer added 4.4 seconds
        # to every "what is this?" (measured 2026-08-19: frame in hand at
        # 1.6 s, tool returning at 6.0 s). The user asks, and nothing happens
        # for six seconds; long enough to assume the camera never fired.
        #
        # Identification is different and still goes the long way: landmark
        # and product lookups are what produce the Maps and shopping links,
        # and those cannot come from the live model's own eyes.
        # `auto` belongs here too, and that is where the time was going.
        #
        # Auto runs landmark detection and then falls back to product. Asked
        # "who is this?" — the commonest thing anyone asks a camera — both
        # find nothing, and they take twelve seconds to find it (measured
        # 2026-08-19: frame in hand at 5.3 s, tool returning at 17.3 s, first
        # word spoken at 18.8 s). The live model then answers from the picture
        # it had all along.
        #
        # So the detector is now reserved for what only it can do: an explicit
        # landmark or product lookup, which is where the Maps and shopping
        # links come from.
        if question or kind == "auto":
            return {
                "ok": True,
                "mode": "direct",
                "_instruction": (
                    "The camera image is already in your context — the frame "
                    "you asked for has just been delivered. Answer the user's "
                    "question from it yourself, now, in one or two sentences. "
                    "Do not call this tool again for the same question."
                ),
            }

        image_data = base64.b64encode(frame).decode("utf-8")
        # CHANGED (UX Spec §3.3): wrap the vision call so a Vision API outage,
        # bad credentials, or quota error becomes a friendly {ok:false,error}
        # the model can speak — instead of a raw "GoogleAPIError: ..." stack
        # string reaching the model via the engine's generic handler. The
        # vision service already returns its own {ok,...} envelope on expected
        # failures; this catch is the last-resort net for the unexpected.
        try:
            result = await run_detection(
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
        # For a recognised place/landmark that came with a location (a Google
        # Maps link), offer to share it: after describing the place, the model
        # asks if the user wants it sent to WhatsApp/Telegram, and on "yes" runs
        # the normal send flow with the Maps link as the message.
        if result.get("ok") and result.get("mode") == "landmark":
            maps_url = _first_landmark_maps_url(result)
            if maps_url:
                result["_instruction"] = (
                    "This place has a location. After you tell the user what "
                    "place it is, ASK them exactly: 'Do you want to send this "
                    "location to your WhatsApp or Telegram?' If they say yes, "
                    "call send_whatsapp (or send_telegram if they prefer) with "
                    "the message text set to the place name followed by its "
                    f"Google Maps link: {maps_url} — then continue the normal "
                    "send flow (ask who to send it to if you don't know yet). "
                    "If they say no, just carry on."
                )
        return result
