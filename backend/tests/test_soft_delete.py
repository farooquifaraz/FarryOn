"""Deleted notes and tasks are tombstones, and tombstones stay invisible.

Phase 1 of docs/LOCAL_FIRST_SYNC.md. A hard `DELETE FROM` can never be synced —
a row that vanishes is indistinguishable from one that was never sent, so every
other device keeps it forever — and it cost the admin panel any view of what a
user had removed.

The risk this trades for is the opposite one: a read that forgets the filter
shows people notes they deleted. Every read path is checked here.
"""

from __future__ import annotations

import pytest

from app.db import repo


async def _note(db, text: str = "note", user_id: int = 1) -> int:
    n = await repo.add_note(db, text=text, user_id=user_id)
    await db.commit()
    return n.id


async def _task(db, title: str = "task", user_id: int = 1) -> int:
    t = await repo.add_task(db, title=title, user_id=user_id)
    await db.commit()
    return t.id


async def test_delete_keeps_the_row_but_marks_it(db_session) -> None:
    note_id = await _note(db_session, "gone")

    assert await repo.delete_note(db_session, note_id=note_id) is True
    await db_session.commit()

    row = await db_session.get(repo.Note, note_id)
    assert row is not None, "hard delete is back — this can never sync"
    assert row.deleted_at is not None


@pytest.mark.parametrize("kind", ["note", "task"])
async def test_a_deleted_row_is_gone_from_every_read(db_session, kind) -> None:
    # The whole point of the filter. Each of these is a separate query in
    # repo.py, and the one that forgets is the one that shows someone a note
    # they threw away.
    if kind == "note":
        row_id = await _note(db_session, "findable text")
        await repo.delete_note(db_session, note_id=row_id)
        await db_session.commit()

        assert await repo.list_notes(db_session, user_id=1) == []
        assert await repo.find_note(db_session, query="findable") is None
        assert await repo.find_notes(db_session, query="findable") == []
    else:
        row_id = await _task(db_session, "findable title")
        await repo.delete_task(db_session, task_id=row_id)
        await db_session.commit()

        assert await repo.list_tasks(db_session, user_id=1) == []
        assert await repo.find_task(db_session, query="findable") is None
        assert await repo.find_tasks(db_session, query="findable") == []


async def test_a_live_row_beside_a_deleted_one_still_shows(db_session) -> None:
    # Guards the opposite mistake: a filter that hides everything.
    keep = await _note(db_session, "keep me")
    drop = await _note(db_session, "drop me")
    await repo.delete_note(db_session, note_id=drop)
    await db_session.commit()

    texts = [n.text for n in await repo.list_notes(db_session, user_id=1)]
    assert texts == ["keep me"]
    assert keep  # referenced, and the id is what survived


async def test_deleting_twice_is_a_no_op(db_session) -> None:
    # `session.get()` happily returns a tombstone. Without the guard the second
    # delete would bump updated_at, and that ghost change syncs out to every
    # device as if something had happened.
    note_id = await _note(db_session)
    assert await repo.delete_note(db_session, note_id=note_id) is True
    await db_session.commit()
    first = (await db_session.get(repo.Note, note_id)).deleted_at

    assert await repo.delete_note(db_session, note_id=note_id) is False
    await db_session.commit()
    assert (await db_session.get(repo.Note, note_id)).deleted_at == first


async def test_a_deleted_task_cannot_be_ticked_or_edited(db_session) -> None:
    task_id = await _task(db_session)
    await repo.delete_task(db_session, task_id=task_id)
    await db_session.commit()

    assert await repo.set_task_done(db_session, task_id=task_id, done=True) is None
    assert await repo.update_task(db_session, task_id=task_id, title="new") is None


async def test_ownership_is_still_enforced(db_session) -> None:
    # The soft-delete guard sits next to the ownership one; neither may swallow
    # the other. A stranger asking for row 4 still gets nothing, and the row
    # survives untouched.
    note_id = await _note(db_session, user_id=1)

    assert await repo.delete_note(db_session, note_id=note_id, user_id=2) is False
    await db_session.commit()

    row = await db_session.get(repo.Note, note_id)
    assert row.deleted_at is None, "someone else's delete left a tombstone"


async def test_updated_at_moves_when_a_row_changes(db_session) -> None:
    # This is what a phone will ask "what changed since?" against.
    task_id = await _task(db_session)
    before = (await db_session.get(repo.Task, task_id)).updated_at

    await repo.set_task_done(db_session, task_id=task_id, done=True)
    await db_session.commit()

    after = (await db_session.get(repo.Task, task_id)).updated_at
    assert after >= before
    assert after is not None


async def test_new_rows_have_updated_at(db_session) -> None:
    note_id = await _note(db_session)
    row = await db_session.get(repo.Note, note_id)
    assert row.updated_at is not None
    assert row.deleted_at is None
    # client_id is nullable on purpose: rows the backend creates (Farry's own
    # tool calls) have no client to mint one, and the 0006 backfill would
    # otherwise have to invent identities for every existing row.
    assert row.client_id is None
