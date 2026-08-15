"""An address deleted in the admin panel must be usable again.

Deleting a user is a soft delete: the row stays, so the audit log and the
subscription history still point at something real. That is deliberate. What
must NOT follow from it is that the person can never come back — an admin who
removes an account and then re-invites the same person would be stuck, and
there is no way out of it from inside the product.

Reported from the device on 2026-08-15: an account was deleted from the admin
panel and signing up again with the same address answered "something went
wrong. try again."
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.core.responses import AppError
from app.modules.auth import service as auth_service
from app.modules.users import service as users_service

EMAIL = "deleted-then-back@example.test"
PASSWORD = "correct-horse-1"


@pytest.fixture
async def actor(db_session):
    """Somebody allowed to delete. The delete path checks rank, so this needs a
    real super_admin row rather than a bare user."""
    from app.db.models import UserRole
    from app.db.seed import seed_roles_and_permissions

    roles = await seed_roles_and_permissions(db_session)
    admin = await auth_service.register(
        db_session,
        get_settings(),
        email="admin-for-delete@example.test",
        password=PASSWORD,
        display_name="Admin",
    )
    await db_session.flush()
    db_session.add(UserRole(user_id=admin.id, role_id=roles["super_admin"].id))
    await db_session.commit()
    return admin


async def test_the_address_can_sign_up_again(db_session, actor) -> None:
    settings = get_settings()

    first = await auth_service.register(
        db_session, settings, email=EMAIL, password=PASSWORD, display_name="Barira"
    )
    await db_session.commit()

    await users_service.soft_delete_user(db_session, actor=actor, user_id=first.id)
    await db_session.commit()

    # The whole point: this must not raise. A soft delete keeps the row, so a
    # plain unique index on email would refuse here — which is why the email
    # index is partial (live rows only).
    second = await auth_service.register(
        db_session, settings, email=EMAIL, password=PASSWORD, display_name="Barira"
    )
    await db_session.commit()

    assert second.id != first.id, "the returning account must be its own row"
    assert second.deleted_at is None
    assert second.email == EMAIL


async def test_a_live_address_is_still_refused(db_session) -> None:
    # The other half: reuse is allowed ONLY because the first row is deleted.
    # An address still in use must keep getting a clean 409 rather than a
    # second row that would make "which account is this?" unanswerable.
    settings = get_settings()
    await auth_service.register(
        db_session, settings, email="still-here@example.test",
        password=PASSWORD, display_name="Someone",
    )
    await db_session.commit()

    with pytest.raises(AppError) as caught:
        await auth_service.register(
            db_session, settings, email="still-here@example.test",
            password=PASSWORD, display_name="Someone Else",
        )
    assert caught.value.code == "EMAIL_TAKEN"
