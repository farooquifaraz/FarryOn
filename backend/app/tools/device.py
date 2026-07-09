"""Client-executed device-control tools.

Like ``set_camera_zoom``, these act on the phone, not the server: the model
calls them, the backend just validates and acknowledges, and the Flutter app —
which receives every ``tool_call`` — performs the action (mute the mic, toggle
or rotate the camera, end the session). This lets the user run the whole app by
voice: "mute the mic", "turn the camera off", "rotate", "end the session".
"""

from __future__ import annotations

from typing import Any

from app.tools.base import Tool, ToolContext


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
