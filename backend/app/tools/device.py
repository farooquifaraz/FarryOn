"""Client-executed device-control tools.

Like ``set_camera_zoom``, these act on the phone, not the server: the model
calls them, the backend just validates and acknowledges, and the Flutter app —
which receives every ``tool_call`` — performs the action (mute the mic, toggle
or rotate the camera, end the session). This lets the user run the whole app by
voice: "mute the mic", "turn the camera off", "rotate", "end the session".
"""

from __future__ import annotations

import base64
from typing import Any

from app.config import get_settings
from app.logging_conf import get_logger
from app.services.vision import run_detection
from app.tools.base import Tool, ToolContext
from app.tools.capture_feedback import capture_failure_message

logger = get_logger(__name__)


class MuteMicTool(Tool):
    """Mute or unmute the microphone."""

    name = "mute_mic"
    description = (
        "Mute or unmute the microphone. Set muted=true to stop listening, "
        "false to start listening again."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"muted": {"type": "boolean"}},
        "required": ["muted"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        return {"applied": True, "muted": bool(kwargs.get("muted"))}


class SetCameraTool(Tool):
    """Turn the camera on or off."""

    name = "set_camera"
    description = "Turn the device camera on or off (on=true to enable video)."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"on": {"type": "boolean"}},
        "required": ["on"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        return {"applied": True, "on": bool(kwargs.get("on"))}


class CapturePhotoTool(Tool):
    """Take a single photo from the active camera and look at it.

    B3: on the smart glasses there is no continuous video — the model calls
    this to snap a still (the app triggers the glasses shutter). We then wait
    for that frame to arrive so the model answers about what it actually sees.
    """

    name = "capture_photo"
    description = (
        "Take a photo from the camera the user is looking through (their smart "
        "glasses) and look at it. Call this whenever the user asks about "
        "something in front of them — 'what is this?', 'what does this say?', "
        "'read this', 'what am I looking at?', 'describe this' — so you get a "
        "fresh picture before answering. After it returns, describe what you "
        "see in the image."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    #: Question that turns the fresh frame into a concise scene description via
    #: the reliable server-side vision path (not the model's realtime video).
    _DESCRIBE_QUESTION = (
        "Describe the scene in front of the camera concisely: the main "
        "objects, the setting, and anything notable. If there is readable "
        "text, include it."
    )

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        # The app started the capture the moment it saw this tool_call. Wait
        # for the resulting frame so it's in context before the model speaks.
        # The timeout is the session's device-appropriate default; a
        # device-reported capture failure wakes the wait early with a reason.
        got = False
        if ctx.wait_for_frame is not None:
            got = await ctx.wait_for_frame()
        if not got:
            reason = ctx.capture_error() if ctx.capture_error is not None else None
            return {"captured": False, "_instruction": capture_failure_message(reason)}

        # Describe the ACTUAL captured frame via the server-side vision path
        # (a one-shot Gemini vision call on the exact JPEG bytes) instead of
        # relying on the model's realtime-video context. The realtime path is
        # unreliable for a one-shot glasses photo — the model kept describing a
        # PREVIOUS scene it still "remembered" (device-proven 2026-07-11: it
        # described an indoor desk while the fresh photo was a night skyline).
        frame: bytes | None
        if ctx.latest_frame is not None:
            frame, _ = ctx.latest_frame()
        else:
            frame = ctx.last_frame
        if frame:
            try:
                image_b64 = base64.b64encode(frame).decode("utf-8")
                detection = await run_detection(
                    "auto",
                    settings=get_settings(),
                    image_data=image_b64,
                    question=self._DESCRIBE_QUESTION,
                )
                answer = (detection.get("result") or {}).get("answer")
                if detection.get("ok") and answer:
                    return {
                        "captured": True,
                        "description": answer,
                        "_instruction": "This is what the camera actually sees "
                        "right now. Relay it to the user and answer their "
                        "question from it; do not describe anything else.",
                    }
            except Exception as exc:  # noqa: BLE001 - fall back to native vision
                logger.warning("capture_photo.describe_failed", error=repr(exc))

        # Fallback: the describe call was unavailable (no vision key) or failed
        # — let the model use its own view of the just-sent frame.
        return {
            "captured": True,
            "_instruction": "The photo is now in view — describe what you see "
            "and answer the user's question about it.",
        }


class RotateCameraTool(Tool):
    """Rotate the camera between portrait and landscape."""

    name = "rotate_camera"
    description = "Rotate the camera between portrait and landscape orientation."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        return {"applied": True}


class EnableBluetoothTool(Tool):
    """Ask the phone to turn Bluetooth on (e.g. to connect the glasses)."""

    name = "enable_bluetooth"
    description = (
        "Turn on the phone's Bluetooth. Use when the user asks to enable/turn "
        "on Bluetooth or to connect the glasses while Bluetooth is off. On "
        "Android the user sees a quick system 'Allow' prompt to confirm."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        return {"applied": True}


class ConnectGlassesTool(Tool):
    """Connect the saved smart glasses over Bluetooth."""

    name = "connect_glasses"
    description = (
        "Connect the user's saved smart glasses over Bluetooth. Call ONLY "
        "after the user confirms they want the glasses connected (ask first, "
        "e.g. after turning Bluetooth on). Requires Bluetooth to be on."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        return {"applied": True}


class DisconnectGlassesTool(Tool):
    """Disconnect the smart glasses."""

    name = "disconnect_glasses"
    description = (
        "Disconnect the user's smart glasses. Use when the user says to "
        "disconnect / turn off / band karo the glasses."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        return {"applied": True}


class EndSessionTool(Tool):
    """End the live session."""

    name = "end_session"
    description = (
        "End the live session and disconnect — use when the user says to stop, "
        "close, or end the session/conversation."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        return {"applied": True}
