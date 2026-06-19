"""Small repository helpers used by tools and the session layer.

These keep persistence concerns out of the tool implementations and provide a
single place to evolve queries. All functions take an :class:`AsyncSession` and
flush (but do not commit) — commit/rollback is owned by the caller's
session scope (see :func:`app.db.base.get_session`).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Note,
    OutboundMessage,
    Session,
    Task,
    ToolCall,
    Transcript,
    User,
)


async def get_or_create_user(session: AsyncSession, external_id: str) -> User:
    """Return the user with ``external_id``, creating it if absent."""
    result = await session.execute(
        select(User).where(User.external_id == external_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(external_id=external_id)
        session.add(user)
        await session.flush()
    return user


async def create_session_row(
    session: AsyncSession,
    *,
    session_id: str,
    provider: str,
    model: str | None,
    user_id: int | None = None,
    resume_of: str | None = None,
    client_platform: str | None = None,
    device_kind: str | None = None,
) -> Session:
    """Persist a new :class:`Session` audit row for a live connection."""
    row = Session(
        id=session_id,
        provider=provider,
        model=model,
        user_id=user_id,
        resume_of=resume_of,
        client_platform=client_platform,
        device_kind=device_kind,
    )
    session.add(row)
    await session.flush()
    return row


async def close_session_row(session: AsyncSession, session_id: str) -> None:
    """Mark a session as ended (best-effort; no-op if the row is missing)."""
    row = await session.get(Session, session_id)
    if row is not None:
        row.ended_at = datetime.now(timezone.utc)
        await session.flush()


async def add_note(
    session: AsyncSession,
    *,
    text: str,
    user_id: int | None = None,
    session_id: str | None = None,
) -> Note:
    """Persist a :class:`Note`."""
    note = Note(text=text, user_id=user_id, session_id=session_id)
    session.add(note)
    await session.flush()
    return note


async def add_task(
    session: AsyncSession,
    *,
    title: str,
    due_date: str | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> Task:
    """Persist a :class:`Task`."""
    task = Task(
        title=title, due_date=due_date, user_id=user_id, session_id=session_id
    )
    session.add(task)
    await session.flush()
    return task


async def add_outbound_message(
    session: AsyncSession,
    *,
    contact: str,
    text: str,
    user_id: int | None = None,
    session_id: str | None = None,
) -> OutboundMessage:
    """Queue an :class:`OutboundMessage` for (stubbed) delivery."""
    message = OutboundMessage(
        contact=contact,
        text=text,
        user_id=user_id,
        session_id=session_id,
        status="queued",
    )
    session.add(message)
    await session.flush()
    return message


async def add_transcript(
    session: AsyncSession,
    *,
    role: str,
    text: str,
    session_id: str | None = None,
) -> Transcript:
    """Persist a finalized :class:`Transcript` segment."""
    row = Transcript(role=role, text=text, session_id=session_id)
    session.add(row)
    await session.flush()
    return row


async def record_tool_call(
    session: AsyncSession,
    *,
    call_id: str,
    name: str,
    args: dict[str, object],
    ok: bool,
    result: object | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    session_id: str | None = None,
) -> ToolCall:
    """Persist a :class:`ToolCall` audit record."""
    row = ToolCall(
        call_id=call_id,
        name=name,
        args_json=json.dumps(args, default=str),
        result_json=None if result is None else json.dumps(result, default=str),
        ok=ok,
        error=error,
        duration_ms=duration_ms,
        session_id=session_id,
    )
    session.add(row)
    await session.flush()
    return row
