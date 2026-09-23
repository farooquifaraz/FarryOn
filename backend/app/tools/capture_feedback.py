"""Shared mapping from device capture-failure reason codes to spoken feedback.

The mobile app reports a failed camera capture over the ``capture_failed``
control message with a machine-readable ``reason`` code (see ``PROTOCOL.md``).
Vision tools (``capture_photo``, ``identify_image``) translate that code into
a message the model can speak, so the user hears the precise cause ("the
glasses aren't connected") instead of a generic "couldn't get a picture".

Reason codes are the single wire contract between the app and the server; keep
this table in sync with ``GlassesCaptureFailure`` on the Dart side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.tools.base import ToolContext

#: Wire reason code -> user-facing guidance the model should relay.
CAPTURE_FAILURE_MESSAGES: dict[str, str] = {
    "not_connected": (
        "The smart glasses aren't connected over Bluetooth right now. Tell the "
        "user clearly that the glasses' Bluetooth is not connected, and ask them "
        "to turn Bluetooth on and connect the glasses (or say 'connect "
        "glasses'), then try again."
    ),
    "busy": (
        "The glasses camera is busy (likely syncing media or recording). "
        "Ask the user to try again in a few seconds."
    ),
    "capture_timeout": (
        "The glasses didn't take the photo in time — they may be busy or out "
        "of range. Ask the user to try once more."
    ),
    "transfer_stalled": (
        "The photo was taken but its transfer from the glasses stalled. Ask "
        "the user to keep the glasses close to the phone and try again."
    ),
    "empty_image": (
        "The glasses returned an empty photo. Ask the user to try again."
    ),
    "command_failed": (
        "The photo command didn't reach the glasses. Ask the user to check "
        "the glasses connection and try again."
    ),
    # Phone path: the app tried to open the camera for this very question and
    # couldn't — it is in the background (Android takes the camera away) or
    # the camera is held by another app. Reported by the client so the tool
    # answers in ~2 s with the real cause instead of sitting out the full
    # frame budget and answering blind.
    "camera_off": (
        "The phone camera couldn't be opened just now — the app may be in "
        "the background. Ask the user to bring the app to the front (or "
        "switch to the glasses camera) and ask again."
    ),
}

#: Fallback for unknown/missing reason codes (older app builds, plain timeout).
DEFAULT_CAPTURE_FAILURE_MESSAGE = (
    "I couldn't get a fresh look just now. Make sure the camera is on and "
    "pointed at it, then ask again."
)


def capture_failure_message(reason: str | None) -> str:
    """Return the spoken-feedback line for a capture failure ``reason`` code."""
    if reason is None:
        return DEFAULT_CAPTURE_FAILURE_MESSAGE
    return CAPTURE_FAILURE_MESSAGES.get(reason, DEFAULT_CAPTURE_FAILURE_MESSAGE)


#: What the model says when the glasses photo has not arrived within the
#: patience window but may still come. It must not guess: the answer comes in
#: a separate note once the photo lands (app/agent/late_photo.py).
PHOTO_ON_ITS_WAY = (
    "The glasses are still sending the photo — their Bluetooth link is slow "
    "right now. Tell the user in ONE short sentence, in their language, that "
    "the photo is on its way and you will tell them as soon as it arrives. Do "
    "NOT guess or describe anything yet; you have not seen it."
)


async def wait_for_photo(ctx: ToolContext) -> bool:
    """Wait for the capture this tool call triggered: the session default,
    or on the glasses only up to the photo patience."""
    if ctx.wait_for_frame is None:
        return False
    if ctx.photo_patience is None:
        return await ctx.wait_for_frame()
    return await ctx.wait_for_frame(timeout=ctx.photo_patience)


def defer_if_still_coming(
    ctx: ToolContext, reason: str | None, question: str | None
) -> dict[str, Any] | None:
    """Hand an unanswered glasses question to the late-photo tracker.

    Returns the tool result to give the model, or ``None`` when the question
    cannot be deferred (phone camera, or a failure the photo cannot survive —
    then the caller reports the failure as before).
    """
    from app.agent.late_photo import STILL_COMING

    if ctx.defer_photo is None or ctx.photo_patience is None:
        return None
    if reason is not None and reason not in STILL_COMING:
        return None
    ctx.defer_photo(question)
    return {"captured": False, "pending": True, "_instruction": PHOTO_ON_ITS_WAY}
