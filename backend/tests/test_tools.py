"""Tests that each tool persists/returns correctly (in-memory sqlite)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from sqlalchemy import select

from app.db.models import Note, OutboundMessage, Task
from app.tools.base import ToolContext
from app.tools.camera import SetCameraZoomTool
from app.tools.messaging import SendMessageTool
from app.tools.notes import CreateNoteTool
from app.tools.tasks import CreateTaskTool
from app.tools.web_search import WebSearchTool


async def test_set_camera_zoom_clamps_and_acknowledges(db_session) -> None:
    ctx = ToolContext(session=db_session)
    tool = SetCameraZoomTool()

    assert (await tool.run(ctx, level=2.5)) == {"applied": True, "zoom": 2.5}
    # Clamps below 1.0 and above the 8.0 ceiling.
    assert (await tool.run(ctx, level=0.2))["zoom"] == 1.0
    assert (await tool.run(ctx, level=99))["zoom"] == 8.0
    # Tolerates a non-numeric argument rather than raising.
    assert (await tool.run(ctx, level="x"))["zoom"] == 1.0


async def test_list_notes_and_tasks_recall_tools(db_session) -> None:
    from app.tools.recall import ListNotesTool, ListTasksTool

    ctx = ToolContext(session=db_session)
    await CreateNoteTool().run(ctx, text="Buy milk")
    await CreateTaskTool().run(ctx, title="Call dentist")
    await db_session.commit()

    notes = await ListNotesTool().run(ctx)
    assert notes["count"] == 1
    assert notes["notes"][0]["text"] == "Buy milk"

    tasks = await ListTasksTool().run(ctx)
    assert tasks["count"] == 1
    assert tasks["tasks"][0]["title"] == "Call dentist"
    assert tasks["tasks"][0]["done"] is False


async def test_task_management_tools_by_name(db_session) -> None:
    from app.tools.task_manage import (
        CompleteTaskTool,
        DeleteNoteTool,
        DeleteTaskTool,
        UpdateTaskTool,
    )

    ctx = ToolContext(session=db_session)
    created = await CreateTaskTool().run(
        ctx, title="Call the dentist", due_date="2026-07-01T09:00:00"
    )
    await db_session.commit()

    # Complete by a word from the title.
    r = await CompleteTaskTool().run(ctx, task="dentist")
    assert r["ok"] is True and r["id"] == created["id"] and r["done"] is True

    # Edit the reminder time.
    r = await UpdateTaskTool().run(
        ctx, task="dentist", due_date="2026-07-02T10:00:00"
    )
    assert r["ok"] is True and r["due_date"] == "2026-07-02T10:00:00"

    # Unknown item → graceful not-found.
    r = await CompleteTaskTool().run(ctx, task="does-not-exist-xyz")
    assert r["ok"] is False

    # Delete the task.
    r = await DeleteTaskTool().run(ctx, task="dentist")
    assert r["ok"] is True and r["deleted"] is True

    # Delete a note by its text.
    await CreateNoteTool().run(ctx, text="grocery list: milk and eggs")
    await db_session.commit()
    r = await DeleteNoteTool().run(ctx, text="grocery")
    assert r["ok"] is True and r["deleted"] is True


async def test_web_search_falls_back_when_primary_exhausted(monkeypatch) -> None:
    """When the primary provider 429s, the fallback provider is used."""
    from app.tools import web_search as ws_mod

    monkeypatch.setattr(
        ws_mod,
        "get_settings",
        lambda: SimpleNamespace(
            web_search_provider="tavily",
            web_search_api_key="primary",
            web_search_fallback_provider="serper",
            web_search_fallback_api_key="fallback",
        ),
    )

    tool = WebSearchTool()

    async def _exhausted(query: str, key: str):
        raise httpx.HTTPStatusError(
            "429 Too Many Requests",
            request=httpx.Request("POST", "https://api.tavily.com/search"),
            response=httpx.Response(429),
        )

    async def _ok(query: str, key: str):
        return [{"title": "Fallback hit", "url": "u", "snippet": "s"}]

    monkeypatch.setattr(tool, "_tavily", _exhausted)
    monkeypatch.setattr(tool, "_serper", _ok)

    out = await tool.run(ToolContext(session=None), query="weather")
    assert out["provider"] == "serper"
    assert out["results"][0]["title"] == "Fallback hit"


async def test_web_search_mock_when_no_keys(monkeypatch) -> None:
    """With no keys configured, deterministic mock results are returned."""
    from app.tools import web_search as ws_mod

    monkeypatch.setattr(
        ws_mod,
        "get_settings",
        lambda: SimpleNamespace(
            web_search_provider="tavily",
            web_search_api_key=None,
            web_search_fallback_provider=None,
            web_search_fallback_api_key=None,
        ),
    )
    out = await WebSearchTool().run(ToolContext(session=None), query="x")
    assert out["provider"] == "mock"
    assert out["results"]


async def test_web_search_per_session_override(monkeypatch) -> None:
    """A per-session web-search config (from the client) overrides env."""
    from app.tools import web_search as ws_mod

    # Server env is mock/no-key, but the client supplies a Tavily key.
    monkeypatch.setattr(
        ws_mod,
        "get_settings",
        lambda: SimpleNamespace(
            web_search_provider="mock",
            web_search_api_key=None,
            web_search_fallback_provider=None,
            web_search_fallback_api_key=None,
        ),
    )
    tool = WebSearchTool()

    async def _ok(query: str, key: str):
        return [{"title": "session-result", "url": "u", "snippet": "s"}]

    monkeypatch.setattr(tool, "_tavily", _ok)

    ctx = ToolContext(
        session=None, web_search={"provider": "tavily", "apiKey": "k"}
    )
    out = await tool.run(ctx, query="weather")
    assert out["provider"] == "tavily"
    assert out["results"][0]["title"] == "session-result"


async def test_create_note_persists(db_session) -> None:
    ctx = ToolContext(session=db_session)
    result = await CreateNoteTool().run(ctx, text="Remember the milk")
    await db_session.commit()

    assert result["text"] == "Remember the milk"
    assert isinstance(result["id"], int)

    rows = (await db_session.execute(select(Note))).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "Remember the milk"


async def test_create_task_persists_with_due_date(db_session) -> None:
    ctx = ToolContext(session=db_session)
    result = await CreateTaskTool().run(
        ctx, title="Call dentist", due_date="2026-07-01T09:00:00Z"
    )
    await db_session.commit()

    assert result["title"] == "Call dentist"
    assert result["due_date"] == "2026-07-01T09:00:00Z"

    rows = (await db_session.execute(select(Task))).scalars().all()
    assert len(rows) == 1
    assert rows[0].due_date == "2026-07-01T09:00:00Z"
    assert rows[0].done is False


async def test_create_task_without_due_date(db_session) -> None:
    ctx = ToolContext(session=db_session)
    result = await CreateTaskTool().run(ctx, title="No due date")
    await db_session.commit()
    assert result["due_date"] is None


async def test_send_message_persists_queued(db_session) -> None:
    ctx = ToolContext(session=db_session)
    result = await SendMessageTool().run(
        ctx, contact="Alex", text="On my way"
    )
    await db_session.commit()

    assert result["contact"] == "Alex"
    assert result["status"] == "queued"

    rows = (
        await db_session.execute(select(OutboundMessage))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "On my way"


async def test_web_search_returns_mock_results_offline(db_session) -> None:
    """With WEB_SEARCH_PROVIDER=mock (test env), no network is hit."""
    ctx = ToolContext(session=db_session)
    result = await WebSearchTool().run(ctx, query="weather tomorrow")

    assert result["provider"] == "mock"
    assert result["query"] == "weather tomorrow"
    assert len(result["results"]) >= 1
    assert "title" in result["results"][0]
    assert "url" in result["results"][0]
