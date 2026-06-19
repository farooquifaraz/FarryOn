"""Tests that each tool persists/returns correctly (in-memory sqlite)."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Note, OutboundMessage, Task
from app.tools.base import ToolContext
from app.tools.messaging import SendMessageTool
from app.tools.notes import CreateNoteTool
from app.tools.tasks import CreateTaskTool
from app.tools.web_search import WebSearchTool


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
