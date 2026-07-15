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
