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


async def test_an_address_deleted_twice_is_still_usable(db_session, actor) -> None:
    """The one that actually happened.

    Two soft-deleted rows for one address is ordinary: sign up, get deleted,
    sign up again, get deleted again. The check used to load every row for the
    address and inspect `deleted_at` afterwards, so the second delete made
    `scalar_one_or_none` raise MultipleResultsFound — an unhandled 500, which
    reaches the phone as "something went wrong. try again" with no way past it.
    On live this closed barirafaruqi@gmail.com permanently (2026-08-15).
    """
    settings = get_settings()

    for _ in range(2):
        made = await auth_service.register(
            db_session, settings, email=EMAIL, password=PASSWORD, display_name="Barira"
        )
        await db_session.commit()
        await users_service.soft_delete_user(db_session, actor=actor, user_id=made.id)
        await db_session.commit()

    third = await auth_service.register(
        db_session, settings, email=EMAIL, password=PASSWORD, display_name="Barira"
    )
    await db_session.commit()
    assert third.deleted_at is None


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


async def test_signing_in_after_two_deletions_is_refused_not_a_crash(
    db_session, actor
) -> None:
    """The same fault, reached from the sign-in screen.

    Every address lookup keyed on email had it. A person whose address had
    been deleted twice could not sign up, could not sign in, could not ask for
    a password reset and could not be re-invited by an admin — all four
    answered 500, none of them said anything a user could act on.
    """
    settings = get_settings()
    for _ in range(2):
        made = await auth_service.register(
            db_session, settings, email=EMAIL, password=PASSWORD, display_name="B"
        )
        await db_session.commit()
        await users_service.soft_delete_user(db_session, actor=actor, user_id=made.id)
        await db_session.commit()

    with pytest.raises(AppError) as caught:
        await auth_service.login(
            db_session, settings, email=EMAIL, password=PASSWORD,
            ip="127.0.0.1", user_agent="test",
        )
    assert caught.value.code == "INVALID_CREDENTIALS", (
        "a deleted account must be refused the same way a wrong password is, "
        "not crash the request"
    )


async def test_forgotten_password_after_two_deletions_stays_silent(
    db_session, actor
) -> None:
    settings = get_settings()
    for _ in range(2):
        made = await auth_service.register(
            db_session, settings, email=EMAIL, password=PASSWORD, display_name="B"
        )
        await db_session.commit()
        await users_service.soft_delete_user(db_session, actor=actor, user_id=made.id)
        await db_session.commit()

    # Silence is the contract here — the endpoint must not reveal whether an
    # address exists. A 500 broke that as loudly as an error message would.
    await auth_service.forgot_password(db_session, settings, email=EMAIL)
