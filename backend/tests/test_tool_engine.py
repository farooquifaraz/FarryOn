"""Tests for the tool engine: schema export, validation, dispatch."""

from __future__ import annotations

import asyncio

import pytest

from app.agent.tool_engine import ToolEngine, ToolValidationError
from app.tools import build_default_tools
from app.tools.base import Tool, ToolContext


def _engine() -> ToolEngine:
    return ToolEngine.from_tools(build_default_tools())


def test_build_system_prompt_carries_time_context() -> None:
    """The prompt gives the model 'now' so it can resolve reminder times."""
    from app.prompts.system import build_system_prompt

    local = build_system_prompt("2026-06-21T22:30:00+05:30")
    assert "2026-06-21T22:30:00+05:30" in local
    assert "LOCAL" in local  # instructs it to use the user's timezone

    utc = build_system_prompt(None)
    assert "UTC" in utc


def test_export_schemas_matches_protocol() -> None:
    """Exported schemas must match the canonical PROTOCOL.md definitions."""
    schemas = {s["name"]: s for s in _engine().export_schemas()}
    assert set(schemas) == {
        "create_note",
        "web_search",
        "create_task",
        "send_message",
        "set_camera_zoom",
        "list_notes",
        "list_tasks",
        "complete_task",
        "update_task",
        "delete_task",
        "delete_note",
        "mute_mic",
        "set_camera",
        "rotate_camera",
        "end_session",
        "read_emails",
        "read_email",
        "send_email",
        "get_location",
    }

    assert schemas["create_note"]["parameters"] == {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    assert schemas["web_search"]["parameters"]["required"] == ["query"]
    assert schemas["create_task"]["parameters"]["required"] == ["title"]
    assert "due_date" in schemas["create_task"]["parameters"]["properties"]
    assert schemas["send_message"]["parameters"]["required"] == ["contact", "text"]


def test_validate_accepts_valid_args() -> None:
    cleaned = _engine().validate("create_task", {"title": "Buy milk"})
    assert cleaned == {"title": "Buy milk"}


def test_validate_strips_unknown_keys() -> None:
    cleaned = _engine().validate(
        "create_note", {"text": "hi", "bogus": 1}
    )
    assert cleaned == {"text": "hi"}


def test_validate_missing_required_raises() -> None:
    with pytest.raises(ToolValidationError):
        _engine().validate("create_note", {})


def test_validate_wrong_type_raises() -> None:
    with pytest.raises(ToolValidationError):
        _engine().validate("create_note", {"text": 123})


def test_validate_unknown_tool_raises() -> None:
    with pytest.raises(ToolValidationError):
        _engine().validate("does_not_exist", {})


def test_register_rejects_duplicate() -> None:
    engine = _engine()
    with pytest.raises(ValueError):
        engine.register(next(iter(build_default_tools())))


async def test_dispatch_validation_error_returns_not_ok(db_session) -> None:
    engine = _engine()
    ctx = ToolContext(session=db_session)
    result = await engine.dispatch("create_note", {}, ctx)
    assert result.ok is False
    assert result.error is not None
    assert "required" in result.error


async def test_dispatch_timeout(db_session) -> None:
    """A tool that hangs is cancelled and reported as a timeout."""

    class SlowTool(Tool):
        name = "slow"
        description = "sleeps"
        parameters = {"type": "object", "properties": {}, "required": []}

        async def run(self, ctx: ToolContext, **kwargs):
            await asyncio.sleep(5)

    engine = ToolEngine(timeout_seconds=0.05)
    engine.register(SlowTool())
    ctx = ToolContext(session=db_session)
    result = await engine.dispatch("slow", {}, ctx)
    assert result.ok is False
    assert "timed out" in (result.error or "")


async def test_dispatch_captures_tool_exception(db_session) -> None:
    class BoomTool(Tool):
        name = "boom"
        description = "raises"
        parameters = {"type": "object", "properties": {}, "required": []}

        async def run(self, ctx: ToolContext, **kwargs):
            raise RuntimeError("kaboom")

    engine = ToolEngine()
    engine.register(BoomTool())
    ctx = ToolContext(session=db_session)
    result = await engine.dispatch("boom", {}, ctx)
    assert result.ok is False
    assert "kaboom" in (result.error or "")
