"""Tests for the get_location tool."""

from __future__ import annotations

import pytest

from app.tools.base import ToolContext
from app.tools.location import GetLocationTool

pytestmark = pytest.mark.asyncio


async def test_get_location_without_fix(db_session) -> None:
    """No location cached -> a friendly note, not an error."""
    result = await GetLocationTool().run(ToolContext(session=db_session))
    assert result["ok"] is False
    assert "location" in result["message"].lower()


async def test_get_location_returns_cached(db_session) -> None:
    """A cached fix is returned with coords + address."""
    ctx = ToolContext(
        session=db_session,
        location={"lat": 25.2048, "lng": 55.2708, "address": "Dubai, UAE"},
    )
    result = await GetLocationTool().run(ctx)
    assert result["ok"] is True
    assert result["lat"] == 25.2048
    assert result["lng"] == 55.2708
    assert result["address"] == "Dubai, UAE"


async def test_get_location_partial_coords_only(db_session) -> None:
    """Coords without an address still resolve (address is optional)."""
    ctx = ToolContext(session=db_session, location={"lat": 1.0, "lng": 2.0})
    result = await GetLocationTool().run(ctx)
    assert result["ok"] is True
    assert result["address"] is None
