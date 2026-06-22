"""``get_location`` tool: report the user's current device location.

The device streams its GPS position (and a reverse-geocoded address) over a
``location_update`` message; the session caches the latest value and exposes it
here so the assistant can answer "where am I?" without any round-trip.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import Tool, ToolContext


class GetLocationTool(Tool):
    """Return the device's most recent known location."""

    name = "get_location"
    description = (
        "Get the user's current location (address and coordinates). Use when "
        "they ask where they are, their address, or anything that needs their "
        "current place."
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Return the cached location, or a friendly note if none is available."""
        loc = ctx.location or {}
        lat = loc.get("lat")
        lng = loc.get("lng")
        if lat is None or lng is None:
            return {
                "ok": False,
                "message": (
                    "I don't have your location yet. Make sure location is "
                    "enabled and permission is granted in the app."
                ),
            }
        return {
            "ok": True,
            "lat": lat,
            "lng": lng,
            "address": loc.get("address"),
        }
