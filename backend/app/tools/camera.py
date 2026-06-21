"""``set_camera_zoom`` — a *client-executed* tool.

Unlike notes/tasks (which run on the server), the effect of this tool happens on
the device: the model asks to zoom the camera, the backend simply validates and
acknowledges, and the Flutter app — which already receives every ``tool_call``
event — applies the zoom to its live camera. The next ~1 fps frame then arrives
zoomed, so the model can re-examine a distant or small object.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import Tool, ToolContext

_MIN_ZOOM = 1.0
_MAX_ZOOM = 8.0


class SetCameraZoomTool(Tool):
    """Zoom the live camera in or out to see things more clearly."""

    name = "set_camera_zoom"
    description = (
        "Zoom the device camera to see distant or small objects more clearly. "
        "'level' is a magnification factor: 1.0 is normal (zoomed out), 2.0 is "
        "2x, and so on. Call this when the user asks to zoom in or out, or when "
        "something is too far away to make out — then look again at the next "
        "camera frame."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "level": {
                "type": "number",
                "description": "Magnification factor, 1.0 (normal) to 8.0.",
            }
        },
        "required": ["level"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Validate and acknowledge; the app performs the actual zoom."""
        raw = kwargs.get("level", 1.0)
        try:
            level = float(raw)
        except (TypeError, ValueError):
            level = 1.0
        # Clamp to a sane range; the device clamps further to its own max.
        level = max(_MIN_ZOOM, min(_MAX_ZOOM, level))
        return {"applied": True, "zoom": round(level, 2)}
