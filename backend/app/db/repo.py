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
    Contact,
    DailyUsage,
    Note,
    OutboundMessage,
    Session,
    Task,
    ToolCall,
    Transcript,
    User,
)

#: Counter columns on :class:`DailyUsage` that ``bump_daily_usage`` may touch.
_USAGE_COUNTERS = (
    "voice_seconds", "frames_sent", "text_turns", "web_searches", "image_scans",
)


async def get_daily_usage(
    session: AsyncSession, *, user_key: str, day: str
) -> DailyUsage | None:
    """Return the usage row for ``(user_key, day)``, or None."""
    return await session.get(DailyUsage, (user_key, day))


async def bump_daily_usage(
    session: AsyncSession, *, user_key: str, day: str, **counters: int
) -> DailyUsage:
    """Increment one or more daily-usage counters, creating the row if needed.

    Unknown counter names are ignored. Flushed (not committed) — the caller
    owns the transaction, as everywhere else in this module.
    """
    row = await session.get(DailyUsage, (user_key, day))
    if row is None:
        row = DailyUsage(user_key=user_key, day=day)
        session.add(row)
    for name, amount in counters.items():
        if name in _USAGE_COUNTERS and amount:
            setattr(row, name, (getattr(row, name) or 0) + int(amount))
    await session.flush()
    return row


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


async def list_notes(
    session: AsyncSession, *, user_id: int | None = None, limit: int = 50
) -> list[Note]:
    """Return notes (newest first), optionally scoped to a user."""
    stmt = select(Note).order_by(Note.created_at.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(Note.user_id == user_id)
    return list((await session.execute(stmt)).scalars().all())


async def list_tasks(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    include_done: bool = True,
    limit: int = 50,
) -> list[Task]:
    """Return tasks (open first, then newest), optionally scoped to a user."""
    stmt = select(Task).order_by(Task.done.asc(), Task.created_at.desc()).limit(
        limit
    )
    if user_id is not None:
        stmt = stmt.where(Task.user_id == user_id)
    if not include_done:
        stmt = stmt.where(Task.done.is_(False))
    return list((await session.execute(stmt)).scalars().all())


async def find_task(
    session: AsyncSession, *, query: str, user_id: int | None = None
) -> Task | None:
    """Find the best task matching ``query`` by title (open ones first).

    Case-insensitive substring match — lets the model act on a task by name
    ("the dentist task") without knowing its id.
    """
    stmt = (
        select(Task)
        .where(Task.title.ilike(f"%{query.strip()}%"))
        .order_by(Task.done.asc(), Task.created_at.desc())
    )
    if user_id is not None:
        stmt = stmt.where(Task.user_id == user_id)
    return (await session.execute(stmt)).scalars().first()


async def find_note(
    session: AsyncSession, *, query: str, user_id: int | None = None
) -> Note | None:
    """Find the most recent note whose text matches ``query``."""
    stmt = (
        select(Note)
        .where(Note.text.ilike(f"%{query.strip()}%"))
        .order_by(Note.created_at.desc())
    )
    if user_id is not None:
        stmt = stmt.where(Note.user_id == user_id)
    return (await session.execute(stmt)).scalars().first()


# CHANGED (UX Spec §3.5): plural finders so the manage tools can detect when a
# fuzzy name matches MORE THAN ONE item and ask the user which, instead of
# silently mutating/deleting the first match (which could be the wrong note/task).
async def find_tasks(
    session: AsyncSession,
    *,
    query: str,
    user_id: int | None = None,
    limit: int = 5,
) -> list[Task]:
    """Return up to ``limit`` tasks matching ``query`` (open ones first)."""
    stmt = (
        select(Task)
        .where(Task.title.ilike(f"%{query.strip()}%"))
        .order_by(Task.done.asc(), Task.created_at.desc())
        .limit(limit)
    )
    if user_id is not None:
        stmt = stmt.where(Task.user_id == user_id)
    return list((await session.execute(stmt)).scalars().all())


async def find_notes(
    session: AsyncSession,
    *,
    query: str,
    user_id: int | None = None,
    limit: int = 5,
) -> list[Note]:
    """Return up to ``limit`` notes whose text matches ``query`` (newest first)."""
    stmt = (
        select(Note)
        .where(Note.text.ilike(f"%{query.strip()}%"))
        .order_by(Note.created_at.desc())
        .limit(limit)
    )
    if user_id is not None:
        stmt = stmt.where(Note.user_id == user_id)
    return list((await session.execute(stmt)).scalars().all())


async def save_contact(
    session: AsyncSession,
    *,
    name: str,
    phone: str | None = None,
    telegram_username: str | None = None,
    user_id: int | None = None,
) -> Contact:
    """Create or update a contact by name (so re-saving just fills in fields)."""
    existing = await find_contact(session, query=name, user_id=user_id, exact=True)
    if existing is not None:
        if phone:
            existing.phone = phone
        if telegram_username:
            existing.telegram_username = telegram_username.lstrip("@")
        await session.flush()
        return existing
    contact = Contact(
        name=name.strip(),
        phone=phone,
        telegram_username=(telegram_username or "").lstrip("@") or None,
        user_id=user_id,
    )
    session.add(contact)
    await session.flush()
    return contact


async def find_contact(
    session: AsyncSession,
    *,
    query: str,
    user_id: int | None = None,
    exact: bool = False,
) -> Contact | None:
    """Find a contact by name (case-insensitive; substring unless ``exact``)."""
    q = query.strip()
    pattern = q if exact else f"%{q}%"
    op = Contact.name.ilike(q) if exact else Contact.name.ilike(pattern)
    stmt = select(Contact).where(op).order_by(Contact.updated_at.desc())
    if user_id is not None:
        # Match the user's own contacts AND global/unowned ones (user_id NULL):
        # Telegram contacts captured by the bot /start webhook have no user_id,
        # so without this a session that has one would never find them.
        stmt = stmt.where(
            (Contact.user_id == user_id) | (Contact.user_id.is_(None))
        )
    return (await session.execute(stmt)).scalars().first()


async def upsert_telegram_chat(
    session: AsyncSession,
    *,
    chat_id: str,
    username: str | None = None,
    display_name: str | None = None,
) -> Contact:
    """Save a Telegram chat_id (from the bot ``/start`` webhook) for later sends.

    Matched first by chat_id, then by username; otherwise a new contact is made.
    """
    stmt = select(Contact).where(Contact.telegram_chat_id == str(chat_id))
    contact = (await session.execute(stmt)).scalars().first()
    if contact is None and username:
        stmt2 = select(Contact).where(
            Contact.telegram_username.ilike(username.lstrip("@"))
        )
        contact = (await session.execute(stmt2)).scalars().first()
    if contact is None:
        contact = Contact(name=display_name or username or f"tg:{chat_id}")
        session.add(contact)
    contact.telegram_chat_id = str(chat_id)
    if username:
        contact.telegram_username = username.lstrip("@")
    if display_name and contact.name.startswith("tg:"):
        contact.name = display_name
    await session.flush()
    return contact


async def update_task(
    session: AsyncSession,
    *,
    task_id: int,
    title: str | None = None,
    due_date: str | None = None,
) -> Task | None:
    """Update a task's title and/or due date; returns the row or None."""
    task = await session.get(Task, task_id)
    if task is not None:
        if title is not None:
            task.title = title
        if due_date is not None:
            task.due_date = due_date
        await session.flush()
    return task


async def set_task_done(
    session: AsyncSession, *, task_id: int, done: bool
) -> Task | None:
    """Mark a task done/undone; returns the row or None if missing."""
    task = await session.get(Task, task_id)
    if task is not None:
        task.done = done
        await session.flush()
    return task


async def delete_note(session: AsyncSession, *, note_id: int) -> bool:
    """Delete a note; returns True if a row was removed."""
    note = await session.get(Note, note_id)
    if note is None:
        return False
    await session.delete(note)
    await session.flush()
    return True


async def delete_task(session: AsyncSession, *, task_id: int) -> bool:
    """Delete a task; returns True if a row was removed."""
    task = await session.get(Task, task_id)
    if task is None:
        return False
    await session.delete(task)
    await session.flush()
    return True


async def add_outbound_message(
    session: AsyncSession,
    *,
    contact: str,
    text: str,
    user_id: int | None = None,
    session_id: str | None = None,
    status: str = "queued",
) -> OutboundMessage:
    """Record an outbound message (audit + history). ``status`` encodes the
    channel + outcome, e.g. ``telegram:delivered`` / ``whatsapp:opened``."""
    message = OutboundMessage(
        contact=contact,
        text=text,
        user_id=user_id,
        session_id=session_id,
        status=status,
    )
    session.add(message)
    await session.flush()
    return message


async def list_outbound_messages(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    limit: int = 10,
) -> list[OutboundMessage]:
    """Most recent sent messages (for the 'what did I send' history)."""
    stmt = select(OutboundMessage).order_by(OutboundMessage.created_at.desc())
    if user_id is not None:
        stmt = stmt.where(
            (OutboundMessage.user_id == user_id)
            | (OutboundMessage.user_id.is_(None))
        )
    stmt = stmt.limit(max(1, min(limit, 50)))
    return list((await session.execute(stmt)).scalars().all())


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
